from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from lib.build_debug_packet import (
    _lookup_authority_rows_sqlite,
    _normalize_authority_rows,
    build_debug_packet,
)
from lib.native_fst_helper import query_fst_range, query_fst_value_at_time
from lib.stream_vcd_reader import iter_vcd_changes
from lib.wave_metadata_cache import build_wave_metadata_cache
from lib.waveform_formats import detect_waveform_format


def _load_signal_row(*, signal_db: Path, full_wave_path: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(signal_db)
    try:
        row = conn.execute(
            """
            select signal_id, scope_id, full_scope_path, full_wave_path, local_name, bit_width, value_kind, source_id
            from signal_metadata
            where full_wave_path = ?
            """,
            (full_wave_path,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "signal_id": row[0],
        "scope_id": row[1],
        "full_scope_path": row[2],
        "full_wave_path": row[3],
        "local_name": row[4],
        "bit_width": row[5],
        "value_kind": row[6],
        "source_id": row[7],
    }


def _load_signal_rows(*, signal_db: Path, focus_scope: str | None = None) -> list[dict[str, Any]]:
    conn = sqlite3.connect(signal_db)
    try:
        if focus_scope:
            rows = conn.execute(
                """
                select signal_id, scope_id, full_scope_path, full_wave_path, local_name, bit_width, value_kind, source_id
                from signal_metadata
                where full_scope_path = ?
                """,
                (focus_scope,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select signal_id, scope_id, full_scope_path, full_wave_path, local_name, bit_width, value_kind, source_id
                from signal_metadata
                """
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "signal_id": row[0],
            "scope_id": row[1],
            "full_scope_path": row[2],
            "full_wave_path": row[3],
            "local_name": row[4],
            "bit_width": row[5],
            "value_kind": row[6],
            "source_id": row[7],
        }
        for row in rows
    ]


def _load_or_build_manifest(*, waveform_path: Path, meta_out_dir: Path) -> dict[str, Any]:
    manifest_path = meta_out_dir / "manifest.json"
    if not manifest_path.exists():
        build_wave_metadata_cache(waveform_path=waveform_path, out_dir=meta_out_dir)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _query_vcd_value_at_time(*, vcd_path: Path, source_id: str, t: int) -> dict[str, Any]:
    latest_change = None
    with vcd_path.open("r", encoding="utf-8", errors="ignore") as f:
        for change_t, change_source_id, value in iter_vcd_changes(f, watched_ids={source_id}):
            if change_t > t:
                break
            latest_change = {
                "found": True,
                "t": change_t,
                "value": value,
            }
    if latest_change is None:
        return {"found": False, "t": None, "value": None}
    return latest_change


def _query_vcd_range(*, vcd_path: Path, source_ids: set[str], t_start: int, t_end: int) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    with vcd_path.open("r", encoding="utf-8", errors="ignore") as f:
        for change_t, change_source_id, value in iter_vcd_changes(f, watched_ids=source_ids):
            if change_t < t_start:
                continue
            if change_t > t_end:
                break
            changes.append(
                {
                    "type": "change",
                    "t": change_t,
                    "source_id": change_source_id,
                    "value": value,
                }
            )
    return changes


def query_signal_value_from_fst(
    *,
    waveform_path: Path,
    full_wave_path: str,
    t: int,
    meta_out_dir: Path,
) -> dict[str, Any]:
    if t < 0:
        raise ValueError("time must be >= 0")
    waveform_format = detect_waveform_format(waveform_path)
    manifest = _load_or_build_manifest(waveform_path=waveform_path, meta_out_dir=meta_out_dir)
    signal_db = Path(manifest["tables"]["signal_metadata_db"])
    signal_row = _load_signal_row(signal_db=signal_db, full_wave_path=full_wave_path)
    if signal_row is None:
        raise ValueError(f"signal not found in waveform metadata: {full_wave_path}")
    source_id = signal_row.get("source_id")
    if not source_id:
        raise ValueError(f"signal is missing source_id metadata: {full_wave_path}")

    if waveform_format == "fst":
        helper_value = query_fst_value_at_time(
            fst_path=waveform_path,
            source_id=source_id,
            t=t,
            bit_width=int(signal_row["bit_width"]),
        )
    elif waveform_format == "vcd":
        helper_value = _query_vcd_value_at_time(
            vcd_path=waveform_path,
            source_id=source_id,
            t=t,
        )
    else:
        raise ValueError(f"unsupported waveform format for direct query: {waveform_format}")
    return {
        "version": "0.1",
        "query": {
            "full_wave_path": full_wave_path,
            "t": t,
        },
        "signal": {
            "signal_id": signal_row["signal_id"],
            "full_wave_path": signal_row["full_wave_path"],
            "local_name": signal_row.get("local_name"),
            "bit_width": signal_row.get("bit_width"),
            "value_kind": signal_row.get("value_kind"),
        },
        "value_at_time": {
            "found": helper_value.get("found", False),
            "t": t if helper_value.get("found", False) else None,
            "value": helper_value.get("value"),
            "status": "ok" if helper_value.get("found", False) else "uninitialized_before_time",
        },
        "waveform": {
            "path": manifest["waveform"]["path"],
            "format": manifest["waveform"]["format"],
        },
    }


def build_debug_packet_from_fst(
    *,
    waveform_path: Path,
    t_start: int,
    t_end: int,
    meta_out_dir: Path,
    focus_scope: str | None = None,
    authority: dict[str, Any] | None = None,
    authority_rows: list[dict[str, Any]] | None = None,
    authority_db: str | Path | None = None,
) -> dict[str, Any]:
    if t_start < 0 or t_end < 0:
        raise ValueError("time range must be >= 0")
    if t_end < t_start:
        raise ValueError("t_end must be >= t_start")
    waveform_format = detect_waveform_format(waveform_path)
    manifest = _load_or_build_manifest(waveform_path=waveform_path, meta_out_dir=meta_out_dir)
    signal_db = Path(manifest["tables"]["signal_metadata_db"])
    signal_rows = _load_signal_rows(signal_db=signal_db, focus_scope=focus_scope)
    source_id_to_signal = {
        row["source_id"]: row
        for row in signal_rows
        if row.get("source_id")
    }
    if waveform_format == "fst":
        direct_changes = query_fst_range(
            fst_path=waveform_path,
            source_ids=list(source_id_to_signal),
            t_start=t_start,
            t_end=t_end,
        )
    elif waveform_format == "vcd":
        direct_changes = _query_vcd_range(
            vcd_path=waveform_path,
            source_ids=set(source_id_to_signal),
            t_start=t_start,
            t_end=t_end,
        )
    else:
        raise ValueError(f"unsupported waveform format for direct packet query: {waveform_format}")
    changes = []
    for change in direct_changes:
        signal_row = source_id_to_signal.get(change.get("source_id"))
        if signal_row is None:
            continue
        changes.append(
            {
                "signal_id": signal_row["signal_id"],
                "t": int(change["t"]),
                "value": change["value"],
            }
        )

    if authority_rows is None and authority is None and authority_db is not None:
        touched_paths = list(
            {
                source_id_to_signal[change["source_id"]]["full_wave_path"]
                for change in direct_changes
                if change.get("source_id") in source_id_to_signal
            }
        )
        authority_rows = _lookup_authority_rows_sqlite(authority_db, touched_paths)

    packet = build_debug_packet(
        store={
            "version": manifest["version"],
            "waveform": manifest["waveform"],
            "signals": signal_rows,
            "windows": [
                {
                    "id": "direct",
                    "t_start": t_start,
                    "t_end": t_end,
                    "change_count": len(changes),
                    "active_signal_count": len({change["signal_id"] for change in changes}),
                }
            ],
            "changes": changes,
        },
        authority_rows=_normalize_authority_rows(authority_rows=authority_rows, authority=authority),
        window_id="direct",
        focus_scope=focus_scope,
        scope_already_filtered=True,
    )
    packet["query"] = {
        "focus_scope": focus_scope,
        "t_start": t_start,
        "t_end": t_end,
    }

    missing_source_count = len([row for row in signal_rows if not row.get("source_id")])
    if focus_scope and not signal_rows:
        packet["notes"].append(f"no signals matched focus scope: {focus_scope}")
    if missing_source_count:
        packet["notes"].append(
            f"{missing_source_count} signals in metadata were skipped because source_id was unavailable"
        )
    return packet
