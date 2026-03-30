from __future__ import annotations

import importlib
from typing import Any


def _import_pywellen() -> Any | None:
    try:
        return importlib.import_module("pywellen")
    except ImportError:
        return None


def _load_pywellen():
    pywellen = _import_pywellen()
    if pywellen is not None:
        return pywellen

    raise RuntimeError(
        "pywellen is not available. The main branch requires pywellen in the active Python environment. "
        "Install it there, or check out the no-pywellen branch if you want a workflow that avoids pywellen."
    )


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
