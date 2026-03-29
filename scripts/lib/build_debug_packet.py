from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _normalize_authority_rows(authority_rows: list[dict[str, Any]] | None = None, authority: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if authority_rows is not None:
        return authority_rows
    if authority is None:
        return []
    if "signals" in authority:
        return authority.get("signals", [])
    return list(authority.values())


def _authority_lookup_keys(full_wave_path: str) -> list[str]:
    keys = [full_wave_path]
    if full_wave_path.startswith("TOP."):
        keys.append(full_wave_path[len("TOP.") :])
    return keys


def _lookup_authority_rows_sqlite(authority_db: str | Path, full_wave_paths: list[str]) -> list[dict[str, Any]]:
    candidates: list[str] = []
    seen: set[str] = set()
    for path in full_wave_paths:
        for key in _authority_lookup_keys(path):
            if key not in seen:
                seen.add(key)
                candidates.append(key)
    if not candidates:
        return []
    placeholders = ",".join("?" for _ in candidates)
    conn = sqlite3.connect(authority_db)
    try:
        cols = {row[1] for row in conn.execute("pragma table_info(authority_lookup)").fetchall()}
        select_cols = [
            ("full_signal_name", "full_signal_name"),
            ("module_type", "module_type"),
            ("instance_path", "instance_path"),
            ("local_signal_name", "local_signal_name"),
            ("signal_kind", "signal_kind"),
            ("direction", "direction"),
            ("decl_width_bits", "decl_width_bits"),
            ("source_file", "source_file"),
            ("provenance", "provenance"),
        ]
        select_sql = ", ".join(
            f"{col} as {alias}" if col in cols else f"NULL as {alias}"
            for col, alias in select_cols
        )
        rows = conn.execute(
            f"select {select_sql} from authority_lookup where full_signal_name in ({placeholders})",
            candidates,
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "full_signal_name": row[0],
            "module_type": row[1],
            "instance_path": row[2],
            "local_signal_name": row[3],
            "signal_kind": row[4],
            "direction": row[5],
            "decl_width_bits": row[6],
            "source_file": row[7],
            "provenance": row[8],
        }
        for row in rows
    ]


def _load_signal_rows_sqlite(*, signal_db: str | Path, focus_scope: str | None = None) -> list[dict[str, Any]]:
    conn = sqlite3.connect(signal_db)
    try:
        if focus_scope:
            rows = conn.execute(
                "select signal_id, scope_id, full_wave_path, local_name, bit_width, value_kind from signal_metadata where full_scope_path = ?",
                (focus_scope,),
            ).fetchall()
        else:
            rows = conn.execute(
                "select signal_id, scope_id, full_wave_path, local_name, bit_width, value_kind from signal_metadata"
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "signal_id": row[0],
            "scope_id": row[1],
            "full_wave_path": row[2],
            "local_name": row[3],
            "bit_width": row[4],
            "value_kind": row[5],
        }
        for row in rows
    ]


def _filter_signals_by_scope(*, signals: list[dict[str, Any]], focus_scope: str | None, scope_signal_index: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    if not focus_scope:
        return signals
    if scope_signal_index is None:
        return [signal for signal in signals if signal["full_wave_path"].startswith(focus_scope + ".")]
    signal_by_id = {signal["signal_id"]: signal for signal in signals}
    allowed_ids = set(scope_signal_index.get(focus_scope, []))
    return [signal_by_id[signal_id] for signal_id in allowed_ids if signal_id in signal_by_id]


def build_debug_packet(*, store: dict[str, Any], authority_rows: list[dict[str, Any]], window_id: str, focus_scope: str | None = None) -> dict[str, Any]:
    window = next(w for w in store["windows"] if w["id"] == window_id)
    changes = [c for c in store["changes"] if c["window_id"] == window_id]
    filtered_signals = _filter_signals_by_scope(signals=store["signals"], focus_scope=focus_scope, scope_signal_index=store.get("scope_signal_index"))
    signal_by_id = {s["signal_id"]: s for s in filtered_signals}
    authority_by_path = {row["full_signal_name"]: row for row in authority_rows}
    focus_signals: list[dict[str, Any]] = []
    unresolved_count = 0
    touched_ids: list[str] = []
    for change in changes:
        if change["signal_id"] not in touched_ids:
            touched_ids.append(change["signal_id"])
    for signal_id in touched_ids:
        signal = signal_by_id.get(signal_id)
        if signal is None:
            continue
        signal_changes = [c for c in changes if c["signal_id"] == signal_id]
        rtl = None
        for candidate in _authority_lookup_keys(signal["full_wave_path"]):
            rtl = authority_by_path.get(candidate)
            if rtl is not None:
                break
        if rtl is None:
            unresolved_count += 1
            rtl_info = {"match_status": "unresolved"}
        else:
            rtl_info = {
                "match_status": "exact",
                "module_type": rtl.get("module_type"),
                "source_file": rtl.get("source_file"),
                "local_signal_name": rtl.get("local_signal_name"),
            }
        focus_signals.append(
            {
                "signal_id": signal_id,
                "full_wave_path": signal["full_wave_path"],
                "bit_width": signal.get("bit_width"),
                "changes": signal_changes,
                "rtl": rtl_info,
            }
        )
    notes = []
    if unresolved_count:
        notes.append(f"{unresolved_count} focus signals were unresolved against the RTL authority table")
    return {
        "version": "0.1",
        "query": {"window_id": window_id, "focus_scope": focus_scope},
        "window_summary": {
            "t_start": window["t_start"],
            "t_end": window["t_end"],
            "change_count": window["change_count"],
            "active_signal_count": window["active_signal_count"],
        },
        "focus_signals": focus_signals,
        "notes": notes,
    }


def build_debug_packet_from_manifest(*, manifest: dict[str, Any], authority: dict[str, Any] | None = None, authority_rows: list[dict[str, Any]] | None = None, authority_db: str | Path | None = None, window_id: str, focus_scope: str | None = None) -> dict[str, Any]:
    windows = _load_json(manifest["tables"]["windows"])
    scope_signal_index = None
    scope_signal_index_path = manifest["tables"].get("scope_signal_index")
    if scope_signal_index_path:
        scope_signal_index = _load_json(scope_signal_index_path)
    signals_path = manifest["tables"].get("signals")
    signal_db_path = manifest["tables"].get("signal_metadata_db")
    if signals_path and Path(signals_path).exists():
        signals = _load_json(signals_path)
    elif signal_db_path:
        signals = _load_signal_rows_sqlite(signal_db=signal_db_path, focus_scope=focus_scope)
    else:
        raise ValueError("manifest is missing usable signal metadata")
    window_index = _load_json(manifest["tables"]["window_index"])
    shard = next(row for row in window_index if row["window_id"] == window_id)
    changes = [json.loads(line) for line in Path(shard["path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    store = {
        "version": manifest["version"],
        "waveform": manifest["waveform"],
        "signals": signals,
        "scope_signal_index": scope_signal_index,
        "windows": windows,
        "changes": changes,
    }
    if authority_rows is None and authority is None and authority_db is not None:
        filtered_signals = _filter_signals_by_scope(signals=signals, focus_scope=focus_scope, scope_signal_index=scope_signal_index)
        signal_by_id = {signal["signal_id"]: signal for signal in filtered_signals}
        touched_paths = [signal_by_id[change["signal_id"]]["full_wave_path"] for change in changes if change["signal_id"] in signal_by_id]
        authority_rows = _lookup_authority_rows_sqlite(authority_db, touched_paths)
    return build_debug_packet(store=store, authority_rows=_normalize_authority_rows(authority_rows=authority_rows, authority=authority), window_id=window_id, focus_scope=focus_scope)
