from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any


def _normalize_authority_rows(
    authority_rows: list[dict[str, Any]] | None = None,
    authority: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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


def _window_numeric_id(window_id: str) -> int:
    if not window_id.startswith("w"):
        raise ValueError(f"invalid window id: {window_id}")
    return int(window_id[1:])


def _filter_signals_by_scope(
    *,
    signals: list[dict[str, Any]],
    focus_scope: str | None,
    scope_signal_index: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    if not focus_scope:
        return signals
    if scope_signal_index is None:
        return [signal for signal in signals if signal["full_wave_path"].startswith(focus_scope + ".")]
    signal_by_id = {signal["signal_id"]: signal for signal in signals}
    allowed_ids = set(scope_signal_index.get(focus_scope, []))
    return [signal_by_id[signal_id] for signal_id in allowed_ids if signal_id in signal_by_id]


def build_debug_packet(
    *,
    store: dict[str, Any],
    authority_rows: list[dict[str, Any]],
    window_id: str,
    focus_scope: str | None = None,
    scope_already_filtered: bool = False,
) -> dict[str, Any]:
    window = next(w for w in store["windows"] if w["id"] == window_id)
    changes = store["changes"]
    if scope_already_filtered:
        filtered_signals = store["signals"]
    else:
        filtered_signals = _filter_signals_by_scope(
            signals=store["signals"],
            focus_scope=focus_scope,
            scope_signal_index=store.get("scope_signal_index"),
        )
    signal_by_id = {s["signal_id"]: s for s in filtered_signals}
    authority_by_path = {row["full_signal_name"]: row for row in authority_rows}
    focus_signals: list[dict[str, Any]] = []
    unresolved_count = 0
    changes_by_signal: dict[str, list[dict[str, Any]]] = {}
    for change in changes:
        changes_by_signal.setdefault(change["signal_id"], []).append(change)
    for signal_id, signal_changes in changes_by_signal.items():
        signal = signal_by_id.get(signal_id)
        if signal is None:
            continue
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
