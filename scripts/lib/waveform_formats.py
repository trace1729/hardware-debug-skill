from __future__ import annotations

from pathlib import Path


SUPPORTED_WAVEFORM_FORMATS = {"vcd", "fst"}


def detect_waveform_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".vcd":
        return "vcd"
    if suffix == ".fst":
        return "fst"
    raise ValueError(f"unsupported waveform format for path: {path}")

