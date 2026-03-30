from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterator


SKILL_DIR = Path(__file__).resolve().parents[2]
HELPER_SRC = SKILL_DIR / "scripts" / "native" / "fst_wave_reader.c"
HELPER_OUT = SKILL_DIR / "scripts" / "native" / ".bin" / "fst_wave_reader"
VENDOR_DIR = SKILL_DIR / "third_party" / "gtkwave_fst"
HELPER_SOURCES = [
    HELPER_SRC,
    VENDOR_DIR / "fstapi.c",
    VENDOR_DIR / "fastlz.c",
    VENDOR_DIR / "lz4.c",
    VENDOR_DIR / "fstapi.h",
    VENDOR_DIR / "fastlz.h",
    VENDOR_DIR / "lz4.h",
    VENDOR_DIR / "fst_config.h",
    VENDOR_DIR / "wavealloca.h",
    VENDOR_DIR / "fst_win_unistd.h",
]


def _needs_rebuild() -> bool:
    if not HELPER_OUT.exists():
        return True
    out_mtime = HELPER_OUT.stat().st_mtime_ns
    return any(path.stat().st_mtime_ns > out_mtime for path in HELPER_SOURCES)


def ensure_fst_helper() -> Path:
    if not _needs_rebuild():
        return HELPER_OUT
    HELPER_OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gcc",
        "-O2",
        "-std=gnu11",
        "-I",
        str(VENDOR_DIR),
        str(HELPER_SRC),
        str(VENDOR_DIR / "fstapi.c"),
        str(VENDOR_DIR / "fastlz.c"),
        str(VENDOR_DIR / "lz4.c"),
        "-lz",
        "-o",
        str(HELPER_OUT),
    ]
    subprocess.run(cmd, check=True, cwd=SKILL_DIR)
    return HELPER_OUT


def iter_fst_records(fst_path: Path) -> Iterator[dict[str, Any]]:
    helper = ensure_fst_helper()
    proc = subprocess.Popen(
        [str(helper), "dump", str(fst_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    stderr = ""
    if proc.stderr is not None:
        stderr = proc.stderr.read()
        proc.stderr.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"fst helper failed with exit code {rc}: {stderr.strip()}")

