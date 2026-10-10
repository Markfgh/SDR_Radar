"""Bench entrypoints: probe/RX and explicitly permitted closed-loopback TX only.

Real phase KPIs remain BLOCKED until operator supplied criteria and independent
delayed-echo evidence exist. Full hardware switching and coherent stacking are
intentionally gated; diagnostic transfer success never approves G1.
"""

import ctypes as ct
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy.signal import correlate

from .diagnostics import adc, phase_statistics
from .hardware import LoopbackPermit, MetadataBladeRF, verify_header
from .storage import CaptureWriter, write_json
from .timing import Continuity
from .types import AcquisitionRecord
from .waveforms import chirp, encode_sc16


def run_probe(path):
    radio = MetadataBladeRF()
    try:
        info = radio.open_device()
        radio.force_tx_off()
        snapshot = asdict(info)
        snapshot["metadata_header"] = verify_header()
        snapshot["dll_path"] = radio._dll_path
        snapshot["capabilities_measured"] = {
            "metadata_rx": "UNTESTED",
            "metadata_tx": "UNTESTED",
            "lo_phase_coherence": "UNVERIFIED",
        }
        write_json(Path(path) / "device.json", snapshot)
        write_json(
            Path(path) / "gate_results.json",
            [
                {
                    "test_id": "PROBE",
                    "status": "PASS",
                    "evidence": "device.json",
                    "phase_gate": "BLOCKED",
                }
            ],
        )
        return snapshot
    finally:
        radio.close_device()


def run_rx(config, path, blocks=100, metadata=False):
    if not 1 <= blocks <= 1000:
        raise ValueError("RX block budget must be 1..1000")
    radio = MetadataBladeRF()
    writer = None
    checks = []
    pending = []
    persisting = False
    count = max(config.capture_samples, 32768) if metadata else config.capture_samples
    if blocks * count * 4 > 256 * 1024**2:
        raise ValueError("RX capture RAM budget exceeds 256 MiB")
    tracker = Continuity()

    def persist():
        # File/manifest writes during a 40 MS/s stream can themselves cause
        # overruns. Stop RX, then preserve all buffers and their diagnostics.
        nonlocal persisting
        persisting = True
        radio.stop_streaming()
        for record, check in pending:
            writer.append(record)
            check["adc"] = adc(record.raw) if record.raw.size else None
        write_json(Path(path) / "rx_checks.json", checks)

    try:
        info = radio.open_device()
        radio.force_tx_off()
        applied = radio.configure_rx(
            int(config.centers_hz[0]),
            int(config.fs_sps),
            int(config.bi_hz),
            info.rx_gain_range_db[0],
        )
        if applied.actual_sample_rate_sps != config.fs_sps:
            raise RuntimeError("Actual RX rate differs from requested rate")
        if applied.actual_bandwidth_hz < config.bi_hz:
            raise RuntimeError("Actual RX bandwidth below requested chirp bandwidth")
        env = {
            "device": asdict(info),
            "applied_rx": asdict(applied),
            "metadata": metadata,
            "tx_enabled": False,
            "requested_block_samples": count,
        }
        if metadata:
            env["metadata_abi"] = radio.prepare_metadata()
        writer = CaptureWriter(Path(path) / "capture", config, "HARDWARE", env)
        if metadata:
            radio.start_metadata_rx(buffer_size=32768)
        else:
            radio.start_streaming(enable_tx=False)
        start = time.monotonic()
        for i in range(blocks):
            if time.monotonic() - start > 60:
                raise TimeoutError("RX watchdog")
            if metadata:
                record = radio.read_metadata(count, step=i)
            else:
                raw = np.empty(2 * config.capture_samples, np.int16)
                radio._check(
                    "sync_rx SC16",
                    radio._lib.bladerf_sync_rx(
                        radio._dev,
                        ct.c_void_p(raw.ctypes.data),
                        config.capture_samples,
                        None,
                        1000,
                    ),
                )
                record = AcquisitionRecord(
                    i,
                    "RX_ONLY",
                    config.centers_hz[0],
                    raw,
                    actual_count=config.capture_samples,
                    origin="HARDWARE",
                    metadata={
                        "timestamp_source": "UNAVAILABLE",
                        "digital_epoch_known": False,
                        "applied_rx": asdict(applied),
                    },
                )
            check = tracker.observe(
                record.timestamp,
                record.actual_count,
                count,
                record.status,
            )
            checks.append(check)
            pending.append((record, check))
            if (
                record.status
                or record.actual_count != count
                or (metadata and not check["valid"])
                or record.raw.size == 0
                or np.any(np.abs(record.raw.astype(np.int32)) >= 2028)
                or record.fc_hz != config.centers_hz[0]
            ):
                raise RuntimeError(
                    "RX discontinuity/count/status/clipping; saved raw prefix and error"
                )
        persist()
        pending.clear()
        writer.finish()
        write_json(
            Path(path) / "gate_results.json",
            [
                {
                    "test_id": "RX_TRANSFER",
                    "status": "PASS",
                    "evidence": "rx_checks.json",
                },
                {
                    "test_id": "RX_CONTINUITY",
                    "status": "PASS" if metadata else "BLOCKED",
                    "reason": "metadata measured"
                    if metadata
                    else "No per-buffer timestamps in legacy format",
                },
                {
                    "test_id": "G1_PHASE",
                    "status": "BLOCKED",
                    "reason": "RX-only cannot establish TX/RX coherence",
                },
            ],
        )
        return {
            "blocks": len(checks),
            "metadata": metadata,
            "phase_state": "PHASE_UNVERIFIED",
        }
    except BaseException as error:
        if writer:
            if not persisting:
                persist()
            writer.finish("FAULT", str(error))
        raise
    finally:
        radio.close_device()


def run_loopback(config, path, permit_path, confirm_tx=False, repeats=10, aba=False):
    """Records arbitrary RX and TX_NOW bursts concurrently; no common epoch assumed.

    Phase estimates use a detected template lag, so are diagnostic only. For true
    scheduled alignment use MetadataBladeRF.scheduled_burst with a measured epoch
    relation and known cable delay, following H2 in the test plan.
    """
    if not confirm_tx:
        raise PermissionError(
            "Real TX disabled: explicit --confirm-tx and filled --permit required"
        )
    if not 1 <= repeats <= 100:
        raise ValueError("Repeat budget must be 1..100")
    permit = LoopbackPermit(json.loads(Path(permit_path).read_text(encoding="utf-8")))
    permit.check(config)
    frequencies = list(config.centers_hz)
    if aba:
        if len(frequencies) != 2:
            raise ValueError("A-B-A requires exactly two config centers")
        frequencies.append(frequencies[0])
    if repeats * len(frequencies) > permit.data["max_bursts"]:
        raise PermissionError("Requested bursts exceed permit")
    radio = MetadataBladeRF()
    writer = None
    records = []
    tx_events = []
    stop = threading.Event()
    reader = None
    errors = []
    try:
        info = radio.open_device()
        radio.force_tx_off()
        abi = radio.prepare_metadata()
        if permit.data["tx_gain_db"] != info.tx_gain_range_db[0]:
            raise PermissionError(
                "First loopback requires hardware-reported minimum TX gain"
            )
        writer = CaptureWriter(
            Path(path) / "capture",
            config,
            "HARDWARE",
            {"device": asdict(info), "abi": abi, "permit": permit.data},
        )
        # Independent watchdog can disable RF even while the main thread is blocked.
        timer = threading.Timer(permit.data["max_duration_s"], radio.force_tx_off)
        timer.daemon = True
        timer.start()
        try:
            for step, fc in enumerate(frequencies):
                rx = radio.configure_rx(
                    int(fc),
                    int(config.fs_sps),
                    int(config.bi_hz),
                    info.rx_gain_range_db[0],
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
                    raise RuntimeError("Actual RX/TX rate differs from waveform")
                if min(rx.actual_bandwidth_hz, tx.actual_bandwidth_hz) < config.bi_hz:
                    raise RuntimeError(
                        "Applied analog bandwidth below waveform bandwidth"
                    )
                radio.start_metadata_rx()
                radio.start_permitted_tx(permit, config)
                tracker = Continuity()
                stop.clear()
                count = max(config.capture_samples, 4 * config.pulse_samples)

                def receive(count=count, step=step, rx=rx, tx=tx, tracker=tracker):
                    try:
                        for _ in range(4 * repeats + 16):
                            if stop.is_set():
                                break
                            rec = radio.read_metadata(count, step=step, mode="RADAR")
                            rec.metadata.update(
                                applied_rx=asdict(rx),
                                applied_tx=asdict(tx),
                                requested_count=count,
                                stream_epoch=step,
                                topology="closed_loopback",
                                phase_state="PHASE_UNVERIFIED",
                            )
                            writer.append(rec)
                            records.append(rec)
                            check = tracker.observe(
                                rec.timestamp, rec.actual_count, count, rec.status
                            )
                            if not check["valid"] or adc(rec.raw)["clipping_detected"]:
                                raise RuntimeError(
                                    "Stream discontinuity or clipping; raw saved"
                                )
                    except BaseException as error:
                        errors.append(error)
                        stop.set()
                        radio.force_tx_off()

                reader = threading.Thread(
                    target=receive, name="SWW-loopback-RX", daemon=True
                )
                reader.start()
                # Repeated complete chirps in a burst make boundary detection possible.
                x = chirp(config)
                burst = np.concatenate((np.tile(x, 8), np.zeros(2, complex)))
                raw = encode_sc16(burst)
                waveform_path = Path(path) / f"tx_waveform_step_{step}.npy"
                np.save(waveform_path, raw, allow_pickle=False)
                for repeat in range(repeats):
                    if stop.is_set():
                        raise RuntimeError(f"RX stopped: {errors}")
                    event = radio.scheduled_burst(raw)
                    tx_events.append(
                        {
                            "step": step,
                            "repeat": repeat,
                            "fc_hz": fc,
                            "tx_iq": str(waveform_path),
                            "applied_tx": asdict(tx),
                            **event,
                        }
                    )
                    write_json(Path(path) / "tx_events.json", tx_events)
                radio.force_tx_off()
                stop.set()
                reader.join(timeout=2)
                if reader.is_alive():
                    raise TimeoutError("RX worker did not stop")
                if errors:
                    raise errors[0]
                radio.stop_streaming()
        finally:
            timer.cancel()
            stop.set()
            radio.force_tx_off()
            if reader:
                reader.join(timeout=2)
        writer.finish()
        estimates = []
        x = chirp(config)
        energy = np.vdot(x, x).real
        for rec in records:
            corr = correlate(rec.iq, x, mode="valid", method="fft")
            if not corr.size:
                continue
            lag = int(np.argmax(np.abs(corr)))
            coef = corr[lag] / energy
            segment = rec.iq[lag : lag + x.size]
            confidence = float(
                abs(corr[lag])
                / max(np.sqrt(np.vdot(segment, segment).real * energy), 1e-30)
            )
            estimates.append(
                {
                    "step": rec.step,
                    "fc_hz": rec.fc_hz,
                    "timestamp": rec.timestamp,
                    "template_lag_samples": lag,
                    "phase_rad": float(np.angle(coef)),
                    "amplitude": float(abs(coef)),
                    "confidence": confidence,
                    "adc": adc(rec.raw),
                    "validity": "DIAGNOSTIC_ONLY_NO_APPROVED_SNR_THRESHOLD",
                }
            )
        write_json(Path(path) / "phase_diagnostics.json", estimates)
        write_json(
            Path(path) / "phase_statistics.json",
            [
                {
                    "step": step,
                    "statistics": phase_statistics(
                        [e["phase_rad"] for e in estimates if e["step"] == step]
                    ),
                    "validity": "DIAGNOSTIC_ONLY_INCLUDES_UNQUALIFIED_TEMPLATE_DETECTIONS",
                }
                for step in sorted(set(e["step"] for e in estimates))
            ],
        )
        write_json(
            Path(path) / "gate_results.json",
            [
                {
                    "test_id": "LOOPBACK_TRANSFER",
                    "status": "PASS",
                    "evidence": "capture/manifest.json; tx_events.json",
                },
                {
                    "test_id": "G1_PHASE",
                    "status": "BLOCKED",
                    "reason": "Need approved SNR/phase thresholds, known independent delayed echo, digital epoch and branch calibration",
                },
            ],
        )
        return {
            "records": len(records),
            "tx_bursts": len(tx_events),
            "phase_state": "PHASE_UNVERIFIED",
        }
    except BaseException as error:
        if writer:
            writer.finish("FAULT", str(error))
        raise
    finally:
        stop.set()
        if radio._dev.value:
            radio.force_tx_off()
        if reader:
            reader.join(timeout=2)
        radio.close_device()
