from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.ingest_waveform import _flush_metadata, _parse_vcd_metadata
from lib.native_fst_helper import iter_fst_records
from lib.waveform_formats import detect_waveform_format


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_wave_metadata_cache(*, waveform_path: Path, out_dir: Path, version: str = "0.1") -> Path:
    waveform_format = detect_waveform_format(waveform_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    signals: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}

    if waveform_format == "vcd":
        with waveform_path.open("r", encoding="utf-8", errors="ignore") as f:
            signals, scopes, _ = _parse_vcd_metadata(f)
    else:
        for record in iter_fst_records(waveform_path, command="meta"):
            record_type = record["type"]
            if record_type == "scope":
                scopes.append(record)
                continue
            if record_type == "signal":
                signal_id = f"sig{len(signals)}"
                signals.append(
                    {
                        "signal_id": signal_id,
                        "scope_id": record.get("scope_id"),
                        "full_wave_path": record["full_wave_path"],
                        "local_name": record["local_name"],
                        "bit_width": record["bit_width"],
                        "value_kind": record["value_kind"],
                        "source_id": record["source_id"],
                    }
                )
                continue
            if record_type == "summary":
                summary = record

    _flush_metadata(out_dir, signals, scopes)

    manifest = {
        "kind": "wave_meta",
        "version": version,
        "waveform": {"path": str(waveform_path), "format": waveform_format},
        "summary": {
            "signal_count": len(signals),
            "scope_count": len(scopes),
            "start_time": summary.get("start_time"),
            "end_time": summary.get("end_time"),
        },
        "tables": {
            "signals": str(out_dir / "signals.json"),
            "signal_metadata_db": str(out_dir / "signal_metadata.sqlite3"),
            "scopes": str(out_dir / "scopes.json"),
            "scope_signal_index": str(out_dir / "scope_signal_index.json"),
        },
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path
