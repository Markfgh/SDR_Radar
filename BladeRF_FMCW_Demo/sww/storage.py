"""Append-only capture folders, raw int16 .npy, strict JSON and SHA256 replay."""

import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import __version__
from .config import from_dict
from .types import AcquisitionRecord


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path, value):
    Path(path).write_text(
        json.dumps(clean(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hash():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for file in sorted(root.glob("*.py")) + [
        root.parent / "bladerf_interface.py",
        root.parent / "fmcw.py",
    ]:
        digest.update(file.name.encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


class CaptureWriter:
    def __init__(self, path, config, origin, environment=None):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "iq").mkdir()
        self.manifest = {
            "schema_version": 1,
            "software_version": __version__,
            "created_utc": datetime.now(UTC).isoformat(),
            "source_hash": source_hash(),
            "origin": origin,
            "config": config.to_dict(),
            "config_hash": config.digest,
            "records": [],
            "state": "INCOMPLETE",
            "environment": environment
            or {"python": platform.python_version(), "platform": platform.platform()},
        }
        self.flush()

    def flush(self):
        temp = self.path / "manifest.tmp"
        write_json(temp, self.manifest)
        # Windows antivirus/indexers can briefly hold the previous manifest.
        # Preserve atomic replace and fail after a bounded retry; never lose IQ.
        for attempt in range(6):
            try:
                temp.replace(self.path / "manifest.json")
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * 2**attempt)

    def append(self, record):
        if record.raw.dtype != np.int16 or record.raw.ndim != 1 or record.raw.size % 2:
            raise ValueError("Raw capture must be interleaved int16")
        if record.mode not in ("CAL_PRE", "RADAR", "CAL_POST", "RX_ONLY"):
            raise ValueError("Unknown acquisition mode")
        name = f"iq/{len(self.manifest['records']):06d}_{record.step}_{record.mode}.npy"
        target = self.path / name
        np.save(target, record.raw, allow_pickle=False)
        meta = {k: v for k, v in vars(record).items() if k != "raw"}
        meta.update(path=name, sha256=sha(target))
        self.manifest["records"].append(meta)
        self.flush()

    def finish(self, state="COMPLETED", error=None, truth=None):
        self.manifest.update(
            state=state,
            error=error,
            truth=truth,
            closed_utc=datetime.now(UTC).isoformat(),
        )
        self.flush()


def replay(path):
    path = Path(path).resolve()
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported capture schema")
    if (
        manifest.get("source_hash") != source_hash()
        or manifest.get("software_version") != __version__
    ):
        raise ValueError(
            "Processing source hash mismatch: use recorded code revision for numerical replay"
        )
    config = from_dict(manifest["config"])
    if config.digest != manifest["config_hash"]:
        raise ValueError("Configuration hash mismatch")
    records = []
    for item in manifest["records"]:
        item = dict(item)
        file = (path / item.pop("path")).resolve()
        if not file.is_relative_to(path):
            raise ValueError("Capture path escapes its directory")
        if sha(file) != item.pop("sha256"):
            raise ValueError("Raw I/Q hash mismatch")
        raw = np.load(file, allow_pickle=False)
        if raw.dtype != np.int16 or raw.ndim != 1 or raw.size % 2:
            raise ValueError("Invalid raw I/Q format")
        records.append(AcquisitionRecord(raw=raw, **item))
    return config, records, manifest
