#!/usr/bin/env python3
"""
disasm_trace.py - 从 XiangShan difftest simulator_out.txt 中提取并反汇编指令

文件结构说明：
  每次 difftest 触发时，会 fork 出 parent/child 两个进程，各自输出一次：
      Commit Group Trace (Core 0)   —— 16个 commit group，记录每组 commit 的起始PC和数量
      Commit Instr Trace            —— 32条具体 commit 指令（含寄存器写回数据）
  两次输出内容相同，是同一次错误的重复记录（parent 检测到错误，child dump波形）。
  本脚本将两者配对为一个 Section，只输出第一次（去重）。

用法:
    python3 disasm_trace.py <simulator_out.txt>
    python3 disasm_trace.py <simulator_out.txt> --objdump riscv64-linux-gnu-objdump
    python3 disasm_trace.py <simulator_out.txt> --output result.txt

输出:
    Part 1: 每个 Section 的 Commit Group Trace + Commit Instr Trace（带反汇编）
    Part 2: 去重后按 PC 排序的汇编表
    Part 3: Difftest 寄存器不一致信息
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile


OBJDUMP = "riscv64-linux-gnu-objdump"

DIFF_RE = re.compile(
    r"(\w+)\s+different\s+at\s+pc\s*=\s*(0x[0-9a-fA-F]+),\s*"
    r"right\s*=\s*(0x[0-9a-fA-F]+),\s*wrong\s*=\s*(0x[0-9a-fA-F]+)"
)
COMMIT_LINE_RE = re.compile(
    r"\[(\d+)\]\s+commit\s+pc\s+([0-9a-fA-F]+)\s+inst\s+([0-9a-fA-F]+)(.*)"
)
GROUP_LINE_RE = re.compile(
    r"commit\s+group\s+\[(\d+)\]:\s+pc\s+([0-9a-fA-F]+)\s+cmtcnt\s+(\d+)(.*)"
)


def disassemble_one(pc, inst_hex, objdump=OBJDUMP):
    """反汇编单条指令，返回汇编助记符字符串"""
    val = int(inst_hex, 16)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        fname = f.name
        f.write(struct.pack("<I", val))
    try:
        r = subprocess.run(
            [objdump, "-b", "binary", "-m", "riscv:rv64gc", f"--adjust-vma=0x{pc:x}", "-D", fname],
            capture_output=True,
            text=True,
        )
        for line in r.stdout.splitlines():
            if f"{pc:x}:" in line:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    return "\t".join(parts[2:])
    finally:
        os.unlink(fname)
    return "<decode failed>"


def build_disasm_cache(filepath, objdump=OBJDUMP):
    """扫描文件，对所有出现的 (pc, inst_hex) 构建反汇编缓存（去重调用）"""
    cache = {}
    with open(filepath, "r") as f:
        for line in f:
            m = COMMIT_LINE_RE.search(line)
            if m:
                pc = int(m.group(2), 16)
                inst_hex = m.group(3).lower()
                key = (pc, inst_hex)
                if key not in cache:
                    cache[key] = disassemble_one(pc, inst_hex, objdump)
    return cache


def parse_sections(filepath):
    """
    解析文件，将每对 (Commit Group Trace, Commit Instr Trace) 配对为一个 section。

    文件中每次 difftest 触发输出两对（parent + child fork），内容相同。
    本函数按内容去重，只保留唯一的 section。
    """
    sections = []
    seen_keys = set()

    lines = open(filepath).readlines()
    i = 0
    n = len(lines)

    while i < n:
        stripped = lines[i].rstrip("\n").strip()

        if "== Commit Group Trace" in stripped:
            group_lines = []
            i += 1
            while i < n:
                s = lines[i].rstrip("\n")
                if s.strip() == "" or "==" in s:
                    break
                if GROUP_LINE_RE.search(s):
                    group_lines.append(s)
                i += 1

            instr_lines = []
            while i < n:
                s = lines[i].rstrip("\n").strip()
                if "== Commit Instr Trace ==" in s:
                    i += 1
                    while i < n:
                        s2 = lines[i].rstrip("\n")
                        if s2.strip() == "" or "==" in s2:
                            break
                        if COMMIT_LINE_RE.search(s2):
                            instr_lines.append(s2)
                        i += 1
                    break
                elif s and not s.startswith("="):
                    break
                i += 1

            if group_lines and instr_lines:
                key = tuple(l.strip() for l in instr_lines)
                if key not in seen_keys:
                    seen_keys.add(key)
                    sections.append({"group_lines": group_lines, "instr_lines": instr_lines})
            continue

        i += 1

    return sections


def decode_diff_errors(filepath):
    """提取 difftest 报告的寄存器不一致信息（去重）"""
    errors = []
    seen = set()
    with open(filepath, "r") as f:
        for line in f:
            m = DIFF_RE.search(line)
            if m:
                key = (m.group(1), m.group(2), m.group(3), m.group(4))
                if key not in seen:
                    seen.add(key)
                    errors.append(
                        {"reg": m.group(1), "pc": m.group(2), "right": m.group(3), "wrong": m.group(4)}
                    )
    return errors


def format_section(sec, sec_idx, cache):
    width = 92
    lines_out = []
    lines_out.append("═" * width)
    lines_out.append(f"  Section {sec_idx}")
    lines_out.append("═" * width)
    lines_out.append("  ┌─ Commit Group Trace (Core 0)")
    lines_out.append("  │")
    for raw in sec["group_lines"]:
        m = GROUP_LINE_RE.search(raw)
        if m:
            grp_id = m.group(1)
            grp_pc = m.group(2)
            cmtcnt = m.group(3)
            rest = m.group(4).rstrip()
            marker = "  <--" if "<--" in rest else ""
            lines_out.append(f"  │  commit group [{grp_id:>02}]: pc {grp_pc:<12}  cmtcnt {cmtcnt}{marker}")
        else:
            lines_out.append(f"  │  {raw.strip()}")
    lines_out.append("  │")
    lines_out.append("  └─ Commit Instr Trace")
    lines_out.append("")
    lines_out.append(
        f'  {"[NN]":<6} {"PC":<18} {"Encoding":<10} {"wen":>3} {"dst":>3} {"data":<18} {"idx":<8}  {"Assembly"}'
    )
    lines_out.append("  " + "─" * (width - 2))

    for raw in sec["instr_lines"]:
        m = COMMIT_LINE_RE.search(raw)
        if not m:
            lines_out.append(f"  {raw.strip()}")
            continue
        idx_str = m.group(1)
        pc = int(m.group(2), 16)
        inst_hex = m.group(3).lower()
        rest = m.group(4).rstrip()
        wen_m = re.search(r"wen\s+(\d+)", rest)
        dst_m = re.search(r"dst\s+(\d+)", rest)
        data_m = re.search(r"data\s+([0-9a-fA-F]+)", rest)
        ridx_m = re.search(r"idx\s+([0-9a-fA-F]+)", rest)
        extra_m = re.search(r"idx\s+[0-9a-fA-F]+\s*(.*)", rest)

        wen = wen_m.group(1) if wen_m else "-"
        dst = dst_m.group(1) if dst_m else "-"
        data = data_m.group(1) if data_m else "-"
        ridx = ridx_m.group(1) if ridx_m else "-"
        extra = extra_m.group(1).strip() if extra_m else ""
        marker = ""
        if "<--" in extra:
            extra = extra.replace("<--", "").strip()
            marker = "  ◄"
        asm = cache.get((pc, inst_hex), "<decode failed>")
        extra_str = f" {extra}" if extra else ""
        lines_out.append(
            f"  [{idx_str:>2}]  {pc:#018x}  {inst_hex:<10} {wen:>3} {dst:>3} "
            f"{data:<18} {ridx:<8}{extra_str:<6}  {asm}{marker}"
        )

    lines_out.append("")
    return lines_out


def main():
    parser = argparse.ArgumentParser(description="反汇编 XiangShan difftest simulator_out.txt 中的 Commit Trace")
    parser.add_argument("input", help="simulator_out.txt 路径")
    parser.add_argument("--objdump", default=OBJDUMP, help=f"RISC-V objdump 路径 (默认: {OBJDUMP})")
    parser.add_argument("--output", default=None, help="结果输出到文件（默认打印到终端）")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)

    print("正在反汇编...", file=sys.stderr)
    cache = build_disasm_cache(args.input, args.objdump)
    sections = parse_sections(args.input)
    if not sections:
        print("ERROR: 未找到 Commit Group/Instr Trace 数据", file=sys.stderr)
        sys.exit(1)

    width = 92
    out_lines = []
    out_lines.append("═" * width)
    out_lines.append(f"  Source : {args.input}")
    out_lines.append(f"  Unique sections found: {len(sections)}  (fork duplicates removed)")
    out_lines.append("═" * width)
    out_lines.append("")
    out_lines.append("【Part 1】Commit Group Trace + Commit Instr Trace with Disassembly")
    out_lines.append("")
    for i, sec in enumerate(sections, 1):
        out_lines += format_section(sec, i, cache)
    out_lines.append("═" * width)
    out_lines.append("【Part 2】Unique Instructions (sorted by PC)")
    out_lines.append("─" * width)
    out_lines.append(f'  {"PC":<18}  {"Encoding":<10}  {"Assembly"}')
    out_lines.append("  " + "─" * 60)
    for (pc, inst_hex), asm in sorted(cache.items(), key=lambda x: x[0][0]):
        out_lines.append(f"  {pc:#018x}  {inst_hex:<10}  {asm}")
    out_lines.append("")
    errors = decode_diff_errors(args.input)
    out_lines.append("═" * width)
    out_lines.append("【Part 3】Difftest Register Mismatches")
    out_lines.append("─" * width)
    if errors:
        for e in errors:
            out_lines.append(f'  reg={e["reg"]:<4}  pc={e["pc"]}    right={e["right"]}    wrong={e["wrong"]}')
    else:
        out_lines.append("  (none)")
    out_lines.append("═" * width)
    result = "\n".join(out_lines)
    if args.output:
        with open(args.output, "w") as f:
            f.write(result + "\n")
        print(f"结果已保存到: {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
