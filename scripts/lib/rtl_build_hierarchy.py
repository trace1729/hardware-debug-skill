from __future__ import annotations

from lib.rtl_models import HierarchySignalRow
from lib.rtl_models import ModuleDef


def build_signal_hierarchy(
    modules: dict[str, ModuleDef],
    top_name: str = "SimTop",
    include_stats: bool = False,
) -> list[HierarchySignalRow] | tuple[list[HierarchySignalRow], dict[str, int]]:
    if top_name not in modules:
        raise KeyError(f"top module not found: {top_name}")

    rows: list[HierarchySignalRow] = []
    template_cache: dict[str, list[tuple[str, str, str | None, int, str]]] = {}

    def module_template(module: ModuleDef) -> list[tuple[str, str, str | None, int, str]]:
        cached = template_cache.get(module.name)
        if cached is not None:
            return cached
        cached = [
            (signal.name, signal.kind, signal.direction, signal.width_bits, module.source_file)
            for signal in module.signals
        ]
        template_cache[module.name] = cached
        return cached

    def walk(module_name: str, instance_path: str) -> None:
        module = modules[module_name]
        for signal_name, signal_kind, direction, width_bits, source_file in module_template(module):
            rows.append(
                HierarchySignalRow(
                    module_type=module.name,
                    instance_path=instance_path,
                    local_signal_name=signal_name,
                    full_signal_name=f"{instance_path}.{signal_name}",
                    signal_kind=signal_kind,
                    direction=direction,
                    decl_width_bits=width_bits,
                    source_file=source_file,
                )
            )
        for inst in module.instances:
            child_path = f"{instance_path}.{inst.instance_name}"
            if inst.module_type in modules:
                walk(inst.module_type, child_path)

    walk(top_name, top_name)
    if include_stats:
        return rows, {"cached_module_template_count": len(template_cache)}
    return rows
