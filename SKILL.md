---
name: hardware-debug-waveform
description: Use when analyzing a hardware failure from emitted RTL, a XiangShan Chisel tree, and a large VCD waveform, especially when the user needs artifact planning, exact commands, waveform-to-RTL lookup, or rough signal-to-Chisel recovery.
---

# Hardware Debug Waveform

## Overview
Use this skill to turn a failing hardware run into a repeatable debug workflow.

It expects:
- waveform VCD path
- Chisel source root such as `src/main/scala/xiangshan`
- optionally emitted RTL root such as `build/rtl`

Optionally accept a debug suggestion, focus scope, or suspected module.

Accuracy note:
- providing `build/rtl` is strongly preferred because it enables exact RTL authority lookup and usually improves analysis accuracy substantially
- without `build/rtl`, the workflow still works in waveform-only mode, but RTL ownership stays unresolved unless another mapping artifact is supplied

## First Step
Run the skill-local CLI before any heavy analysis.

All commands below are written relative to the skill root folder. Enter the skill root first, then run the scripts from there:

```bash
cd hardware-debug-waveform
python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /path/to/src/main/scala/xiangshan \
  --vcd /path/to/run.vcd \
  [--rtl-root /path/to/build/rtl] \
  [--focus-scope TOP....rob] \
  [--suggestion "hang near rob tail"]
```

The skill-local `inspect-inputs` command:
- validates required paths
- prints file and tree sizes
- prints the exact artifact storage locations
- checks cache status before expensive rebuilds
- warns when artifacts are large
- prints the exact commands to build or reuse debug artifacts

If it warns that files are very large, tell the user before running expensive commands.

## Core Workflow
1. Build the waveform DB from the VCD.
2. If `build/rtl` is available, build RTL authority artifacts from emitted RTL. This is the preferred path because it improves RTL-side accuracy.
3. Query a debug packet for the chosen window and optional focus scope.
4. Use the packet to reason about failing signals.
5. Map packet signals back:
   - exact: waveform signal -> RTL owner through `rtl_authority.sqlite3`
   - rough: RTL module + local signal -> Chisel candidate through the rough mapping artifact if available
   - if no `build/rtl` is available, do waveform-only analysis and clearly label RTL ownership as unavailable

## Commands
The helper prints exact commands, but the main ones are:

```bash
python scripts/hw_debug_cli.py build-authority --rtl-root <rtl-root> --top SimTop [--out-dir <authority-out>] [--force]
python scripts/hw_debug_cli.py build-wave-db --vcd <vcd> [--out-dir <wave-out>] --window-len 1000 [--force]
python scripts/hw_debug_cli.py query-packet --manifest <wave-out>/manifest.json --window-id <wN> --out <packet.json> [--authority <authority-artifact>] [--focus-scope <scope>]
python scripts/hw_debug_cli.py rough-map-chisel --packet <packet.json> --mapping <rough-mapping.json> --out <rough-join.json>
```

Default storage location:
- artifacts are stored under `hardware-debug-waveform/artifacts/`
- if `--out-dir` is omitted, the skill derives a deterministic cache directory from the input files and options
- the important default subtrees are:
  - `artifacts/authority/<fingerprint>/`
  - `artifacts/wave_db/<fingerprint>/`
  - `artifacts/packets/<fingerprint>/`

Cache behavior:
- `build-authority` reuses an existing cached artifact when the RTL tree signature and `--top` still match
- `build-wave-db` reuses an existing cached artifact when the VCD signature and `--window-len` still match
- use `--force` to rebuild even when the cache matches

If `build/rtl` is unavailable:
- skip `build-authority`
- run `build-wave-db`
- run `query-packet` without `--authority`
- treat all packet RTL fields as unresolved unless rough mapping or other evidence is supplied

Preferred authority artifact:
- use `rtl_authority.sqlite3` first
- fall back to `rtl_authority_index.json` or `rtl_authority_table.json` only if needed

Preferred waveform metadata path:
- use `signal_metadata.sqlite3` when available

## How To Read Waveform And Map Back
When inspecting a debug packet:
1. Read `focus_signals[*].full_wave_path` and `changes`.
2. Treat `rtl.match_status == exact` as authoritative RTL mapping.
3. Use `rtl.module_type` and `rtl.local_signal_name` to identify the emitted RTL owner.
4. If a rough mapping artifact exists from `EmitMappingTable`, join roughly on:
   - `rtl_module == module_type`
   - `rtl_signal == local_signal_name`
5. Present rough Chisel recovery as a guess, not as exact source truth.

Use this wording discipline:
- `exact RTL match`
- `waveform-only analysis`
- `rough Chisel candidate`
- `unresolved`

Do not claim exact Chisel source ownership unless the artifact explicitly proves it.

## Large Artifact Warnings
Warn the user when:
- VCD is multi-GB
- RTL tree contains thousands of files
- authority or waveform DB outputs are likely to exceed hundreds of MB

Recommended warning:
`These artifacts are large; initial preprocessing may take minutes and produce multi-GB outputs, but later packet queries are much cheaper.`

## Output Style
When the user asks for debugging help, produce:
- only a small artifact status summary
- only the packet/query command if it matters for reproducibility
- suspected module or focus scope
- exact RTL findings
- rough Chisel candidates if available
- a short explanation of the likely issue

Prioritize the response in this order:
1. exact RTL analysis
2. likely fault mechanism
3. rough Chisel candidates if they help
4. minimal artifact bookkeeping

Do not spend much output space on artifact inventory, file lists, or preprocessing details unless the user explicitly asks for them.
