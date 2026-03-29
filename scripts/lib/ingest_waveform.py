from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
import sqlite3
from typing import IO, Any

from lib.stream_vcd_reader import iter_vcd_changes


def _parse_vcd_metadata(lines: Iterable[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    scopes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    id_to_signal: dict[str, str] = {}
    scope_stack: list[tuple[str, str]] = []
    scope_counter = 0
    signal_counter = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line == "$enddefinitions $end":
            break
        if line.startswith("$scope "):
            parts = line.split()
            scope_kind = parts[1]
            scope_name = parts[2]
            parent_scope_id = scope_stack[-1][0] if scope_stack else None
            full_scope_path = ".".join([s[1] for s in scope_stack] + [scope_name])
            scope_id = f"scope{scope_counter}"
            scope_counter += 1
            scopes.append(
                {
                    "scope_id": scope_id,
                    "full_scope_path": full_scope_path,
                    "parent_scope_id": parent_scope_id,
                    "scope_kind": scope_kind,
                    "local_name": scope_name,
                }
            )
            scope_stack.append((scope_id, scope_name))
            continue
        if line.startswith("$upscope"):
            if scope_stack:
                scope_stack.pop()
            continue
        if line.startswith("$var "):
            parts = line.split()
            bit_width = int(parts[2])
            id_code = parts[3]
            local_name = parts[4]
            scope_id = scope_stack[-1][0] if scope_stack else None
            full_wave_path = ".".join([s[1] for s in scope_stack] + [local_name])
            signal_id = f"sig{signal_counter}"
            signal_counter += 1
            signals.append(
                {
                    "signal_id": signal_id,
                    "vcd_id_code": id_code,
                    "scope_id": scope_id,
                    "full_wave_path": full_wave_path,
                    "local_name": local_name,
                    "bit_width": bit_width,
                    "value_kind": "scalar" if bit_width == 1 else "vector",
                }
            )
            id_to_signal[id_code] = signal_id
    return signals, scopes, id_to_signal


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_scope_signal_index(signals: list[dict[str, Any]], scopes: list[dict[str, Any]]) -> dict[str, list[str]]:
    scope_path_by_id = {scope["scope_id"]: scope["full_scope_path"] for scope in scopes}
    index: dict[str, list[str]] = {}
    for signal in signals:
        scope_id = signal.get("scope_id")
        if scope_id is None:
            continue
        scope_path = scope_path_by_id.get(scope_id)
        if scope_path is None:
            continue
        index.setdefault(scope_path, []).append(signal["signal_id"])
    return index


def _write_signal_metadata_sqlite(path: Path, signals: list[dict[str, Any]], scopes: list[dict[str, Any]]) -> None:
    scope_path_by_id = {scope["scope_id"]: scope["full_scope_path"] for scope in scopes}
    conn = sqlite3.connect(path)
    try:
        conn.execute("drop table if exists signal_metadata")
        conn.execute(
            """
            create table signal_metadata (
                signal_id text primary key,
                scope_id text,
                full_scope_path text,
                full_wave_path text,
                local_name text,
                bit_width integer,
                value_kind text
            )
            """
        )
        conn.executemany(
            """
            insert into signal_metadata(
                signal_id, scope_id, full_scope_path, full_wave_path, local_name, bit_width, value_kind
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    signal["signal_id"],
                    signal.get("scope_id"),
                    scope_path_by_id.get(signal.get("scope_id")),
                    signal["full_wave_path"],
                    signal["local_name"],
                    signal["bit_width"],
                    signal["value_kind"],
                )
                for signal in signals
            ],
        )
        conn.execute("create index signal_metadata_scope_idx on signal_metadata(full_scope_path)")
        conn.commit()
    finally:
        conn.close()


def stream_waveform_store(*, vcd_path: Path, out_dir: Path, window_len: int, version: str = "0.1") -> Path:
    if window_len < 1:
        raise ValueError("window_len must be >= 1")
    out_dir.mkdir(parents=True, exist_ok=True)
    with vcd_path.open("r", encoding="utf-8", errors="ignore") as f:
        signals, scopes, id_to_signal = _parse_vcd_metadata(f)
    _write_json(out_dir / "signals.json", signals)
    _write_json(out_dir / "scopes.json", scopes)
    _write_json(out_dir / "scope_signal_index.json", _build_scope_signal_index(signals, scopes))
    _write_signal_metadata_sqlite(out_dir / "signal_metadata.sqlite3", signals, scopes)
    by_window_dir = out_dir / "changes" / "by_window"
    by_window_dir.mkdir(parents=True, exist_ok=True)

    watched_ids = set(id_to_signal.keys())
    signal_window_map: dict[tuple[str, str], dict[str, Any]] = {}
    window_map: dict[str, dict[str, Any]] = {}
    window_file_map: dict[str, IO[str]] = {}
    change_count = 0
    try:
        with vcd_path.open("r", encoding="utf-8", errors="ignore") as f:
            for t, id_code, value in iter_vcd_changes(f, watched_ids=watched_ids):
                signal_id = id_to_signal[id_code]
                window_idx = t // window_len
                window_id = f"w{window_idx}"
                change = {"t": t, "signal_id": signal_id, "window_id": window_id, "value": value}
                window_file = window_file_map.get(window_id)
                if window_file is None:
                    window_file = (by_window_dir / f"{window_id}.jsonl").open("w", encoding="utf-8")
                    window_file_map[window_id] = window_file
                window_file.write(json.dumps(change, sort_keys=True) + "\n")
                change_count += 1
                win = window_map.get(window_id)
                if win is None:
                    window_map[window_id] = {
                        "id": window_id,
                        "t_start": window_idx * window_len,
                        "t_end": ((window_idx + 1) * window_len) - 1,
                        "change_count": 1,
                        "active_signal_ids": {signal_id},
                    }
                else:
                    win["change_count"] += 1
                    win["active_signal_ids"].add(signal_id)
                key = (signal_id, window_id)
                row = signal_window_map.get(key)
                if row is None:
                    signal_window_map[key] = {
                        "signal_id": signal_id,
                        "window_id": window_id,
                        "first_t": t,
                        "last_t": t,
                        "change_count": 1,
                    }
                else:
                    row["last_t"] = t
                    row["change_count"] += 1
    finally:
        for window_file in window_file_map.values():
            window_file.close()

    windows = []
    for window_id in sorted(window_map, key=lambda wid: int(wid[1:])):
        win = window_map[window_id]
        windows.append(
            {
                "id": win["id"],
                "t_start": win["t_start"],
                "t_end": win["t_end"],
                "change_count": win["change_count"],
                "active_signal_count": len(win["active_signal_ids"]),
            }
        )
    signal_window_index = sorted(signal_window_map.values(), key=lambda row: (row["signal_id"], row["window_id"]))
    _write_json(out_dir / "windows.json", windows)
    _write_json(out_dir / "signal_window_index.json", signal_window_index)
    window_index = [
        {"window_id": window["id"], "path": str(by_window_dir / f"{window['id']}.jsonl"), "change_count": window["change_count"]}
        for window in windows
    ]
    _write_json(out_dir / "window_index.json", window_index)
    manifest = {
        "version": version,
        "waveform": {"path": str(vcd_path), "format": "vcd"},
        "summary": {
            "signal_count": len(signals),
            "scope_count": len(scopes),
            "window_count": len(windows),
            "change_count": change_count,
        },
        "tables": {
            "signals": str(out_dir / "signals.json"),
            "signal_metadata_db": str(out_dir / "signal_metadata.sqlite3"),
            "scopes": str(out_dir / "scopes.json"),
            "scope_signal_index": str(out_dir / "scope_signal_index.json"),
            "windows": str(out_dir / "windows.json"),
            "window_changes_dir": str(by_window_dir),
            "window_index": str(out_dir / "window_index.json"),
            "signal_window_index": str(out_dir / "signal_window_index.json"),
        },
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path
