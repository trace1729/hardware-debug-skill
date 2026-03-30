from __future__ import annotations

import importlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import IO, Any


def _bundled_pywellen_root() -> Path:
    return Path(__file__).resolve().parents[2] / "wellen" / "pywellen"


def _import_pywellen() -> Any | None:
    try:
        return importlib.import_module("pywellen")
    except ImportError:
        return None


def _import_pywellen_from_bundled_root() -> Any | None:
    bundled_root = _bundled_pywellen_root()
    if not bundled_root.exists():
        return None
    if str(bundled_root) not in sys.path:
        sys.path.insert(0, str(bundled_root))
    return _import_pywellen()


def _load_pywellen():
    pywellen = _import_pywellen()
    if pywellen is not None:
        return pywellen

    pywellen = _import_pywellen_from_bundled_root()
    if pywellen is not None:
        return pywellen

    raise RuntimeError(
        "pywellen is not available. The skill tried the active Python environment and the bundled local "
        "copy under wellen/pywellen but could not load it."
    )


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _normalize_format(file_format: str) -> str:
    return file_format.lower()


def _normalize_value(value: Any, bit_width: int | None) -> str:
    if isinstance(value, int):
        if bit_width is None or bit_width <= 1:
            return str(value)
        return format(value, f"0{bit_width}b")
    return str(value)


def _collect_metadata(waveform: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    hierarchy = waveform.hierarchy
    scopes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    signal_by_full_path: dict[str, Any] = {}
    scope_counter = 0
    signal_counter = 0

    def visit_scope(scope: Any, parent_scope_id: str | None) -> None:
        nonlocal scope_counter, signal_counter
        scope_id = f"scope{scope_counter}"
        scope_counter += 1
        full_scope_path = scope.full_name(hierarchy)
        scopes.append(
            {
                "scope_id": scope_id,
                "full_scope_path": full_scope_path,
                "parent_scope_id": parent_scope_id,
                "scope_kind": scope.scope_type(),
                "local_name": scope.name(hierarchy),
            }
        )

        for var in scope.vars(hierarchy):
            full_wave_path = var.full_name(hierarchy)
            bit_width = var.bitwidth() or 0
            signal_id = f"sig{signal_counter}"
            signal_counter += 1
            signal = {
                "signal_id": signal_id,
                "scope_id": scope_id,
                "full_wave_path": full_wave_path,
                "local_name": var.name(hierarchy),
                "bit_width": bit_width,
                "value_kind": "scalar" if bit_width == 1 else "vector",
            }
            signals.append(signal)
            signal_by_full_path[full_wave_path] = {
                "signal_id": signal_id,
                "var": var,
                "bit_width": bit_width,
            }

        for child in scope.scopes(hierarchy):
            visit_scope(child, scope_id)

    for top_scope in hierarchy.top_scopes():
        visit_scope(top_scope, None)

    return signals, scopes, signal_by_full_path


def stream_waveform_store_wellen(
    *,
    wave_path: Path,
    out_dir: Path,
    window_len: int,
    version: str = "0.1",
    pywellen_module: Any | None = None,
) -> Path:
    if window_len < 1:
        raise ValueError("window_len must be >= 1")

    pywellen = pywellen_module or _load_pywellen()
    waveform = pywellen.Waveform(path=str(wave_path))

    out_dir.mkdir(parents=True, exist_ok=True)
    signals, scopes, signal_by_full_path = _collect_metadata(waveform)
    _write_json(out_dir / "signals.json", signals)
    _write_json(out_dir / "scopes.json", scopes)
    _write_json(out_dir / "scope_signal_index.json", _build_scope_signal_index(signals, scopes))
    _write_signal_metadata_sqlite(out_dir / "signal_metadata.sqlite3", signals, scopes)

    by_window_dir = out_dir / "changes" / "by_window"
    by_window_dir.mkdir(parents=True, exist_ok=True)

    signal_window_map: dict[tuple[str, str], dict[str, Any]] = {}
    window_map: dict[str, dict[str, Any]] = {}
    window_file_map: dict[str, IO[str]] = {}
    change_count = 0

    try:
        for signal in signals:
            full_wave_path = signal["full_wave_path"]
            signal_info = signal_by_full_path[full_wave_path]
            sig = waveform.get_signal(signal_info["var"])
            for t, value in sig.all_changes():
                normalized_value = _normalize_value(value, signal_info["bit_width"])
                signal_id = signal_info["signal_id"]
                window_idx = t // window_len
                window_id = f"w{window_idx}"
                change = {
                    "t": t,
                    "signal_id": signal_id,
                    "window_id": window_id,
                    "value": normalized_value,
                }
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
        {
            "window_id": window["id"],
            "path": str(by_window_dir / f"{window['id']}.jsonl"),
            "change_count": window["change_count"],
        }
        for window in windows
    ]
    _write_json(out_dir / "window_index.json", window_index)

    manifest = {
        "version": version,
        "waveform": {
            "path": str(wave_path),
            "format": _normalize_format(waveform.hierarchy.file_format()),
        },
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
