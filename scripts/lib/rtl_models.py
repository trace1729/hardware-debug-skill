from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalDef:
    name: str
    kind: str
    direction: str | None
    width_bits: int


@dataclass(frozen=True)
class InstanceDef:
    module_type: str
    instance_name: str


@dataclass(frozen=True)
class ModuleDef:
    name: str
    signals: list[SignalDef]
    instances: list[InstanceDef]
    source_file: str


@dataclass(frozen=True)
class HierarchySignalRow:
    module_type: str
    instance_path: str
    local_signal_name: str
    full_signal_name: str
    signal_kind: str
    direction: str | None
    decl_width_bits: int
    source_file: str
