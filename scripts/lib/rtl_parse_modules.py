from __future__ import annotations

import re
from pathlib import Path

from lib.rtl_models import InstanceDef
from lib.rtl_models import ModuleDef
from lib.rtl_models import SignalDef


_MODULE_RE = re.compile(
    r"module\s+(?P<name>[A-Za-z_]\w*)\s*(?:#\s*\(.*?\)\s*)?\((?P<header>.*?)\)\s*;(?P<body>.*?)endmodule",
    re.DOTALL,
)
_PORT_RE = re.compile(
    r"\b(?P<direction>input|output|inout)\b\s*"
    r"(?:(?:wire|reg|logic)\b\s*)?"
    r"(?P<width>\[[^\]]+\])?\s*"
    r"(?P<name>[A-Za-z_]\w*)\b",
    re.MULTILINE,
)
_DECL_RE = re.compile(
    r"^\s*(?P<kind>wire|reg|logic)\b\s*(?P<width>\[[^\]]+\])?\s*(?P<names>[^;]+);",
    re.MULTILINE,
)


def _strip_comments(text: str) -> str:
    text = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def _width_bits(width: str | None) -> int:
    if not width:
        return 1
    m = re.fullmatch(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", width.strip())
    if not m:
        return 1
    left = int(m.group(1))
    right = int(m.group(2))
    return abs(left - right) + 1


def _parse_signal_names(names_field: str) -> list[str]:
    names: list[str] = []
    for part in names_field.split(","):
        token = part.strip()
        if not token:
            continue
        m = re.match(r"([A-Za-z_]\w*)", token)
        if m:
            names.append(m.group(1))
    return names


def _parse_instances(body: str, known_modules: set[str]) -> list[InstanceDef]:
    instances: list[InstanceDef] = []
    pattern = re.compile(
        r"(^|;)\s*(?P<module>[A-Za-z_]\w*)\s*(?:#\s*\(.*?\)\s*)?(?P<inst>[A-Za-z_]\w*)\s*\(",
        re.DOTALL | re.MULTILINE,
    )
    for match in pattern.finditer(body):
        module_name = match.group("module")
        if module_name not in known_modules:
            continue
        instances.append(InstanceDef(module_type=module_name, instance_name=match.group("inst")))
    instances.sort(key=lambda inst: inst.instance_name)
    return instances


def parse_rtl_files(paths: list[Path]) -> dict[str, ModuleDef]:
    parsed: list[tuple[str, list[SignalDef], str, str]] = []
    for path in paths:
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in _MODULE_RE.finditer(text):
            name = match.group("name")
            header = match.group("header")
            body = match.group("body")

            signals: list[SignalDef] = []
            for port_match in _PORT_RE.finditer(header):
                signals.append(
                    SignalDef(
                        name=port_match.group("name"),
                        kind="port",
                        direction=port_match.group("direction"),
                        width_bits=_width_bits(port_match.group("width")),
                    )
                )

            for decl_match in _DECL_RE.finditer(body):
                width_bits = _width_bits(decl_match.group("width"))
                for signal_name in _parse_signal_names(decl_match.group("names")):
                    signals.append(
                        SignalDef(
                            name=signal_name,
                            kind=decl_match.group("kind"),
                            direction=None,
                            width_bits=width_bits,
                        )
                    )

            parsed.append((name, signals, body, str(path)))

    known_modules = {name for name, _, _, _ in parsed}
    modules: dict[str, ModuleDef] = {}
    for name, signals, body, source_file in parsed:
        modules[name] = ModuleDef(
            name=name,
            signals=signals,
            instances=_parse_instances(body, known_modules),
            source_file=source_file,
        )
    return modules
