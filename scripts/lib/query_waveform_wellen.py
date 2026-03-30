from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.build_debug_packet import (
    _lookup_authority_rows_sqlite,
    _normalize_authority_rows,
    _window_numeric_id,
    build_debug_packet,
)
from lib.ingest_waveform_wellen import _collect_metadata, _load_pywellen, _normalize_value


def _scope_path_candidates(path: str) -> list[str]:
    candidates = [path]
    if path.startswith("TOP."):
        candidates.append(path[len("TOP.") :])
    else:
        candidates.append(f"TOP.{path}")
    return list(dict.fromkeys(candidates))


def _load_waveform(*, wave_path: str | Path, pywellen_module: Any | None = None) -> Any:
    pywellen = pywellen_module or _load_pywellen()
    return pywellen.Waveform(path=str(wave_path))


def _resolve_signal_metadata(*, signal_by_full_path: dict[str, Any], full_wave_path: str) -> tuple[str, dict[str, Any]]:
    for candidate in _scope_path_candidates(full_wave_path):
        signal_info = signal_by_full_path.get(candidate)
        if signal_info is not None:
            return candidate, signal_info
    raise ValueError(f"signal not found in waveform metadata: {full_wave_path}")


def _resolve_focus_signal_ids(*, signals: list[dict[str, Any]], focus_scope: str | None) -> set[str] | None:
    if not focus_scope:
        return None
    allowed_paths = tuple(f"{candidate}." for candidate in _scope_path_candidates(focus_scope))
    selected_ids = {
        signal["signal_id"]
        for signal in signals
        if signal["full_wave_path"].startswith(allowed_paths)
    }
    if not selected_ids:
        raise ValueError(f"focus scope not found in waveform metadata: {focus_scope}")
    return selected_ids


def _load_authority_rows(
    *,
    authority: dict[str, Any] | None = None,
    authority_rows: list[dict[str, Any]] | None = None,
    authority_db: str | Path | None = None,
    touched_paths: list[str],
) -> list[dict[str, Any]]:
    if authority_rows is not None or authority is not None:
        return _normalize_authority_rows(authority_rows=authority_rows, authority=authority)
    if authority_db is None:
        return []
    return _lookup_authority_rows_sqlite(authority_db, touched_paths)


def query_signal_value_from_waveform(
    *,
    wave_path: str | Path,
    full_wave_path: str,
    t: int,
    window_len: int = 1000,
    pywellen_module: Any | None = None,
) -> dict[str, Any]:
    if t < 0:
        raise ValueError("time must be >= 0")
    if window_len < 1:
        raise ValueError("window_len must be >= 1")

    waveform = _load_waveform(wave_path=wave_path, pywellen_module=pywellen_module)
    signals, _scopes, signal_by_full_path = _collect_metadata(waveform)
    resolved_path, signal_info = _resolve_signal_metadata(signal_by_full_path=signal_by_full_path, full_wave_path=full_wave_path)
    signal_row = next(signal for signal in signals if signal["signal_id"] == signal_info["signal_id"])
    sig = waveform.get_signal(signal_info["var"])

    latest_change = None
    for change_t, value in sig.all_changes():
        if change_t > t:
            break
        latest_change = {
            "t": change_t,
            "value": _normalize_value(value, signal_info["bit_width"]),
        }

    window_idx = t // window_len
    return {
        "version": "0.1",
        "query": {
            "full_wave_path": full_wave_path,
            "t": t,
        },
        "signal": {
            "signal_id": signal_row["signal_id"],
            "full_wave_path": resolved_path,
            "local_name": signal_row.get("local_name"),
            "bit_width": signal_row.get("bit_width"),
            "value_kind": signal_row.get("value_kind"),
        },
        "window": {
            "id": f"w{window_idx}",
            "t_start": window_idx * window_len,
            "t_end": ((window_idx + 1) * window_len) - 1,
        },
        "value_at_time": {
            "found": latest_change is not None,
            "t": latest_change["t"] if latest_change is not None else None,
            "value": latest_change["value"] if latest_change is not None else None,
            "status": "ok" if latest_change is not None else "uninitialized_before_time",
        },
    }


def build_debug_packet_from_waveform(
    *,
    wave_path: str | Path,
    window_id: str,
    window_len: int,
    focus_scope: str | None = None,
    authority: dict[str, Any] | None = None,
    authority_rows: list[dict[str, Any]] | None = None,
    authority_db: str | Path | None = None,
    pywellen_module: Any | None = None,
) -> dict[str, Any]:
    if window_len < 1:
        raise ValueError("window_len must be >= 1")

    waveform = _load_waveform(wave_path=wave_path, pywellen_module=pywellen_module)
    signals, _scopes, signal_by_full_path = _collect_metadata(waveform)

    focus_signal_ids = _resolve_focus_signal_ids(signals=signals, focus_scope=focus_scope)
    if focus_signal_ids is None:
        focus_signals = signals
    else:
        focus_signals = [signal for signal in signals if signal["signal_id"] in focus_signal_ids]

    window_idx = _window_numeric_id(window_id)
    t_start = window_idx * window_len
    t_end = ((window_idx + 1) * window_len) - 1

    changes: list[dict[str, Any]] = []
    active_signal_ids: set[str] = set()
    touched_focus_paths: set[str] = set()

    for signal in signals:
        signal_info = signal_by_full_path[signal["full_wave_path"]]
        sig = waveform.get_signal(signal_info["var"])
        for change_t, value in sig.all_changes():
            if change_t < t_start:
                continue
            if change_t > t_end:
                break
            normalized_value = _normalize_value(value, signal_info["bit_width"])
            changes.append(
                {
                    "t": change_t,
                    "signal_id": signal["signal_id"],
                    "window_id": window_id,
                    "value": normalized_value,
                }
            )
            active_signal_ids.add(signal["signal_id"])
            if focus_signal_ids is None or signal["signal_id"] in focus_signal_ids:
                touched_focus_paths.add(signal["full_wave_path"])

    authority_rows_resolved = _load_authority_rows(
        authority=authority,
        authority_rows=authority_rows,
        authority_db=authority_db,
        touched_paths=sorted(touched_focus_paths),
    )
    store = {
        "version": "0.1",
        "waveform": {"path": str(wave_path)},
        "signals": focus_signals,
        "windows": [
            {
                "id": window_id,
                "t_start": t_start,
                "t_end": t_end,
                "change_count": len(changes),
                "active_signal_count": len(active_signal_ids),
            }
        ],
        "changes": changes,
    }
    return build_debug_packet(
        store=store,
        authority_rows=authority_rows_resolved,
        window_id=window_id,
        focus_scope=focus_scope,
        scope_already_filtered=True,
    )


def load_authority_object(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
