"""Bounded CAL_PRE -> RADAR -> CAL_POST FSM with persisted raw data and fail closed.

Mock validation is executable. Physical switching requires measured stream-mute
and position detection; this sequencer rejects unqualified real backends.
"""

import time
from datetime import UTC, datetime


class Sequencer:
    def __init__(self, backend, switch, writer, max_seconds=60, max_steps=64):
        self.backend = backend
        self.switch = switch
        self.writer = writer
        self.max_seconds = max_seconds
        self.max_steps = max_steps
        self.events = []
        self.state = "DISCONNECTED"
        self.cancelled = False

    def transition(self, state, **details):
        self.state = state
        self.events.append(
            {"state": state, "utc": datetime.now(UTC).isoformat(), **details}
        )
        self.writer.manifest["events"] = self.events
        self.writer.flush()

    def stop(self):
        self.cancelled = True
        self.backend.disable_tx()

    def run(self, config):
        start = time.monotonic()
        records = []
        try:
            config.validate()
            if not self.backend.simulated:
                raise RuntimeError(
                    "BLOCKED_SWITCH: real stream mute, controller and phase continuity not qualified"
                )
            if len(config.centers_hz) > self.max_steps:
                raise ValueError("Step budget exceeded")
            self.transition("PROBED")
            self.transition("RX_READY")
            self.transition("ARMED_TX", origin="SIMULATED")
            for step, fc in enumerate(config.centers_hz):
                self.backend.mute()
                self.backend.tune(step, fc)
                epoch = self.backend.epoch
                for mode in ("CAL_PRE", "RADAR", "CAL_POST"):
                    if self.cancelled or time.monotonic() - start > self.max_seconds:
                        raise TimeoutError("Abort/watchdog")
                    self.backend.mute()
                    position = "RADAR" if mode == "RADAR" else "CAL"
                    self.switch.select(position)
                    if self.switch.read_position() != position:
                        raise RuntimeError("Switch position mismatch")
                    if self.backend.epoch != epoch:
                        raise RuntimeError("Retune/reset inside CAL triplet")
                    self.transition(mode, step=step, fc_hz=fc, switch=position)
                    record = self.backend.capture(step, mode)
                    self.writer.append(record)
                    records.append(record)
                    if (
                        record.status
                        or record.actual_count != config.capture_samples
                        or record.fc_hz != fc
                    ):
                        raise RuntimeError("Stream integrity failure")
                    from .diagnostics import adc

                    if adc(record.raw)["clipping_detected"]:
                        raise RuntimeError("ADC clipping")
                    if mode == "CAL_PRE":
                        from .diagnostics import reference_snr

                        if (
                            reference_snr(record.iq, config.pulse_samples)
                            < config.min_reference_snr_db
                        ):
                            raise RuntimeError("INVALID_PHASE: reference SNR")
                self.transition(
                    "VALIDATED_STEP", step=step, evidence="STREAM_ONLY_PHASE_PENDING"
                )
                # Reject degraded endpoints before tuning the next step.
                from dataclasses import replace

                from .stacking import stack

                stack(
                    replace(
                        config,
                        centers_hz=(fc,),
                        step_weights=config.step_weights[step : step + 1],
                    ),
                    [replace(r, step=0) for r in records[-3:]],
                )
            self.transition("COMPLETED")
            self.writer.finish()
            return records
        except BaseException as error:
            self.backend.disable_tx()
            self.transition(
                "ABORTED"
                if isinstance(error, (KeyboardInterrupt, TimeoutError))
                else "FAULT",
                error=str(error),
            )
            self.writer.finish(state=self.state, error=str(error))
            raise
        finally:
            self.backend.disable_tx()
