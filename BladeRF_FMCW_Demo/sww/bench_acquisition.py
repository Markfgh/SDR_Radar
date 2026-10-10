"""Bounded bench bursts with continuous metadata RX within each frequency visit.

No RF at import. A separate, expiring bench permit covers the SUMMED_ANTENNAS
topology explicitly. Existing closed-loopback permissions are not broadened.
RX timestamps measure RX continuity; TX_NOW never claims a shared TX/RX epoch.
"""

import json
import math
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .hardware import LoopbackPermit, MetadataBladeRF
from .storage import CaptureWriter, sha, write_json
from .timing import Continuity
from .waveforms import chirp, encode_sc16


class BenchPermit(LoopbackPermit):
    def __init__(self, data, session_hash, stage):
        super().__init__(data)
        self.session_hash = session_hash
        self.stage = stage

    def check(self, config):
        d = self.data
        required = (
            "operator_approved",
            "connections_checked",
            "power_budget_verified",
            "environment_verified",
            "emergency_stop_available",
        )
        if not all(d.get(k) is True for k in required):
            raise PermissionError(
                "Bench RF connection/power/environment preflight incomplete"
            )
        if d.get("session_sha256") != self.session_hash or self.stage not in d.get(
            "authorized_stages", []
        ):
            raise PermissionError("Permit does not authorize this session/stage")
        if not d.get("operator") or not d.get("evidence"):
            raise PermissionError("Named operator and measured RF evidence required")
        topology = d.get("topology")
        if topology == "summed_antennas":
            if not all(
                d.get(k) is True
                for k in (
                    "antenna_tx_explicitly_authorized",
                    "both_combiner_inputs_budgeted",
                    "splitter_combiner_isolation_checked",
                )
            ):
                raise PermissionError(
                    "Antennas require explicit radiation authorization and BOTH RX branches budgeted"
                )
        elif topology == "closed_loopback":
            if d.get("closed_terminated_loopback") is not True:
                raise PermissionError("Closed/terminated loopback not verified")
        else:
            raise PermissionError("Unknown RF topology")
        if (
            self.stage in ("reference_pre", "reference_post")
            and d.get("rx_echo_branch_terminated") is not True
        ):
            raise PermissionError(
                "Reference-only capture requires echo RX branch terminated and evidence"
            )
        if (
            self.stage in ("fixed", "aba", "sweep")
            and d.get("independent_path_present") is not True
        ):
            raise PermissionError("Independent known path must be physically present")
        try:
            expiry = datetime.fromisoformat(d["expires_utc"])
        except (KeyError, TypeError, ValueError):
            raise PermissionError("Valid UTC permit expiry required") from None
        if expiry.tzinfo is None or datetime.now(UTC) >= expiry:
            raise PermissionError("Bench permit expired or has no timezone")
        # Reuse the legacy RF budget checks WITHOUT pretending antennas are a
        # closed loopback. Its own topology authorization remains unchanged.
        amplitude = d.get("digital_amplitude_max")
        if (
            not isinstance(amplitude, (int, float))
            or not math.isfinite(amplitude)
            or not 0 < amplitude <= 0.05
            or config.amplitude > amplitude
        ):
            raise PermissionError("Invalid TX digital amplitude budget")
        if (
            not 0 < d.get("max_duration_s", 0) <= 60
            or not 0 < d.get("max_bursts", 0) <= 1000
        ):
            raise PermissionError("Finite duration/burst budget required")
        if type(d.get("tx_gain_db")) is not int:
            raise PermissionError("Exact TX gain required")
        powers = [
            d.get(k)
            for k in (
                "estimated_max_rx_dbm",
                "verified_rx_limit_dbm",
                "required_rx_margin_db",
            )
        ]
        if (
            not all(type(v) in (int, float) and math.isfinite(v) for v in powers)
            or powers[2] <= 0
            or powers[0] + powers[2] >= powers[1]
        ):
            raise PermissionError(
                "Measured worst-case TOTAL analog RX power/margin required"
            )
        band = d.get("allowed_band_hz", [])
        if (
            len(band) != 2
            or not all(type(v) in (int, float) and math.isfinite(v) for v in band)
            or not band[0] < band[1]
        ):
            raise PermissionError("Finite ordered authorized band required")
        if not all(
            band[0] <= f - config.bi_hz / 2 and f + config.bi_hz / 2 <= band[1]
            for f in config.centers_hz
        ):
            raise PermissionError("Chirp extends outside authorized band")
        self.check_time()


def capture_stage(config, path, frequencies, repeats, permit, session_hash, stage):
    """Save every RX block, including noise, before doing any bench DSP.

    The reader remains active across bursts at a fixed frequency. Buffers are
    retained in bounded RAM during RF, then persisted with TX disabled. The fixed
    test has no internal stream restart; A-B-A deliberately restarts on retune,
    and logs this as a condition being qualified rather than hiding it.
    """
    permit.check(config)
    if repeats * len(frequencies) > permit.data["max_bursts"]:
        raise PermissionError("Stage exceeds authorized burst budget")
    path = Path(path)
    radio = MetadataBladeRF()
    writer = None
    events = []
    stop = threading.Event()
    reader = None
    timer = None
    stage_error = None
    try:
        info = radio.open_device()
        radio.force_tx_off()
        if info.board not in ("bladerf1", "bladeRF1"):
            raise RuntimeError(f"Expected bladeRF 1.0, got {info.board}")
        abi = radio.prepare_metadata()
        if permit.data["tx_gain_db"] != info.tx_gain_range_db[0]:
            raise PermissionError(
                "Initial bench TX requires device-reported minimum gain"
            )
        writer = CaptureWriter(
            path / "capture",
            config,
            "HARDWARE",
            {
                "device": asdict(info),
                "abi": abi,
                "permit": permit.data,
                "session_sha256": session_hash,
                "stage": stage,
                "tx_rx_epoch": "UNVERIFIED",
                "reference_alignment": "MEASURED_RX_RELATIVE",
            },
        )
        waveform = encode_sc16(np.concatenate([chirp(config), np.zeros(2, complex)]))
        np.save(path / "tx_waveform.npy", waveform, allow_pickle=False)
        timer = threading.Timer(permit.data["max_duration_s"], radio.force_tx_off)
        timer.daemon = True
        timer.start()
        for visit, fc in enumerate(frequencies):
            permit.check(config)
            radio.force_tx_off()
            rx = radio.configure_rx(
                int(fc), int(config.fs_sps), int(config.bi_hz), info.rx_gain_range_db[0]
            )
            tx = radio.configure_tx(
                int(fc),
                int(config.fs_sps),
                int(config.bi_hz),
                permit.data["tx_gain_db"],
            )
            if (
                rx.actual_sample_rate_sps != config.fs_sps
                or tx.actual_sample_rate_sps != config.fs_sps
            ):
                raise RuntimeError("RX/TX actual sample rate differs from waveform")
            if min(rx.actual_bandwidth_hz, tx.actual_bandwidth_hz) < config.bi_hz:
                raise RuntimeError("RX/TX actual bandwidth below chirp bandwidth")
            if radio.actual_frequency(0) != fc or radio.actual_frequency(1) != fc:
                raise RuntimeError("Actual RF center differs from requested center")
            # Settling is explicit, operator supplied, and excluded from valid RX.
            time.sleep(permit.data["settle_s"])
            radio.start_metadata_rx(buffer_size=32768)
            tracker = Continuity()
            blocks = []
            errors = []
            condition = threading.Condition()
            stop.clear()

            def receive(
                visit=visit,
                fc=fc,
                rx=rx,
                tx=tx,
                blocks=blocks,
                errors=errors,
                condition=condition,
                tracker=tracker,
            ):
                try:
                    while not stop.is_set():
                        rec = radio.read_metadata(32768, step=visit, mode="RADAR")
                        rec.metadata.update(
                            requested_count=32768,
                            frequency_visit=visit,
                            applied_rx=asdict(rx),
                            applied_tx=asdict(tx),
                            stage=stage,
                            stream_restart_before_visit=True,
                            digital_epoch_known=False,
                        )
                        check = tracker.observe(
                            rec.timestamp, rec.actual_count, 32768, rec.status
                        )
                        invalid = (
                            not check["valid"]
                            or rec.fc_hz != fc
                            or np.any(np.abs(rec.raw.astype(np.int32)) >= 2028)
                        )
                        with condition:
                            blocks.append(rec)
                            if invalid:
                                stop.set()
                            condition.notify_all()
                        if invalid:
                            raise RuntimeError(
                                "RX timestamp/count/status/frequency/clipping failure"
                            )
                        if len(blocks) * 32768 * 4 >= 64 * 1024**2:
                            raise RuntimeError(
                                "64 MiB RAM budget per visit exceeded; stop RF"
                            )
                except BaseException as error:
                    errors.append(error)
                    stop.set()
                    try:
                        radio.force_tx_off()
                    finally:
                        with condition:
                            condition.notify_all()

            def await_blocks(target, blocks=blocks, errors=errors, condition=condition):
                deadline = time.monotonic() + 2
                with condition:
                    while len(blocks) < target and not stop.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("RX worker progress timeout")
                        condition.wait(min(remaining, 0.1))
                if errors or stop.is_set():
                    raise RuntimeError(f"RX worker stopped: {errors}")
                permit.check_time()

            reader = threading.Thread(
                target=receive, name="bench-metadata-RX", daemon=True
            )
            reader.start()
            visit_error = None
            try:
                await_blocks(2)
                radio.start_permitted_tx(permit, config)
                for repeat in range(repeats):
                    await_blocks(len(blocks) + 2)
                    before = len(blocks)
                    event = radio.scheduled_burst(
                        waveform
                    )  # explicit TX_NOW, no epoch claim
                    await_blocks(len(blocks) + 8)
                    events.append(
                        {
                            "visit": visit,
                            "repeat": repeat,
                            "fc_hz": fc,
                            "rx_blocks_before_tx": before,
                            "rx_blocks_after_tx": len(blocks),
                            "host_monotonic_s": time.monotonic(),
                            **event,
                        }
                    )
            except BaseException as error:
                visit_error = error
                raise
            finally:
                stop.set()
                radio.force_tx_off()
                reader.join(timeout=2)
                if reader.is_alive():
                    raise TimeoutError("RX thread failed to join; stop bank physically")
                # Save all valid-prefix/raw failing buffers, even on Ctrl-C.
                for rec in blocks:
                    writer.append(rec)
                write_json(path / "tx_events.json", events)
                radio.stop_streaming()
                if errors and visit_error is None:
                    raise errors[0]
        writer.finish()
        write_json(
            path / "waveform.json",
            {"sha256": sha(path / "tx_waveform.npy"), "trailing_complex_zeros": 2},
        )
    except BaseException as error:
        stage_error = error
        raise
    finally:
        stop.set()
        if timer:
            timer.cancel()
        try:
            radio.force_tx_off()
        finally:
            if reader:
                reader.join(timeout=2)
            radio.close_device()
            if writer and stage_error:
                writer.finish("FAULT", str(stage_error))
    return {"rx_records": len(writer.manifest["records"]), "tx_bursts": len(events)}


def load_permit(path, session_hash, stage, config):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    settle = data.get("settle_s")
    if (
        type(settle) not in (int, float)
        or not math.isfinite(settle)
        or not 0 < settle <= 1
    ):
        raise PermissionError("Measured settling time in (0,1] s required")
    permit = BenchPermit(data, session_hash, stage)
    permit.check(config)
    return permit
