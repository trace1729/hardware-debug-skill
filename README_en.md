# Waveform Analysis Skill 

## Summary

This skill provides a structured hardware-debug workflow that combines waveform evidence, emitted RTL, and Scala/Chisel source code for LLM-assisted analysis.

The `main` branch uses `wellen` / `pywellen` for direct waveform queries. Install `pywellen` in the active Python environment. If you want to avoid `pywellen`, check out the `no-pywellen` branch instead.



## How To Use

### Install

```bash
# codex
mkdir -p ~/.codex/skills/
cd ~/.codex/skills

```bash
# or claude code
mkdir -p ~/.claude/skills/
cd ~/.claude/skills/
```

```bash
pip install pywellen
git clone https://github.com/trace1729/hardware-debug-skill.git hardware-debug-waveform
```

Notes:

- the `main` branch expects `pywellen` to be importable from the active Python environment
- if you want a workflow that avoids `pywellen`, use the `no-pywellen` branch

### Simple Usage

```text
codex
$Hardware Debug Waveform help me debug xxx.vcd
$Hardware Debug Waveform explain this module with xxx.vcd
```

Required inputs:

- `--scala-root`: Scala/Chisel source tree
- `--waveform`: waveform path for `inspect-inputs`, `query-packet`, and `query-signal-value`

Common optional inputs:

- `--rtl-root`: **recommand** emitted RTL root, usually `build/rtl`
- `--focus-scope`: waveform hierarchy scope to narrow analysis
- `--suggestion`: human debug hint
- `--top`: RTL top module name, default `SimTop`
- `--window-len`: time-window length, default `1000`

## Basic Pipeline

### How the LLM uses the artifacts under the hood

1. `inspect-inputs` validates paths, estimates artifact size, and prints the recommended commands.
2. If emitted RTL is available, `build-authority` creates an exact RTL ownership database.
3. The preferred path uses `query-packet --waveform` or `query-signal-value --waveform`, which reads the waveform directly through `wellen`.
4. The generated packet keeps only the relevant changes for the selected window and can attach exact RTL ownership.
5. The LLM then uses `module_type`, `local_signal_name`, and `focus_scope` to locate the relevant Scala/Chisel source.
6. The direct query path reuses metadata cached under `artifacts/waveform_meta/`.

For FST input, only the direct `--waveform` path should be used.

## Detailed Analysis

### Subcommand Introduction

#### `inspect-inputs`

Checks inputs and prints the recommended next commands.

- validates `--scala-root`, `--waveform`, and `--rtl-root`
- estimates waveform and source-tree size
- prints default artifact locations
- prints direct waveform query commands first

#### `build-authority`

Builds exact RTL authority from emitted RTL.

- parses `.sv` and `.v`
- reconstructs instance hierarchy
- expands hierarchical signal names
- writes JSON and SQLite lookup artifacts

#### `query-packet`

Builds one debug packet for one time window.

- preferred mode: `--waveform`
- optional `--authority` join
- optional `--focus-scope` narrowing
- direct packet queries on very large FST files may be slow

#### `query-signal-value`

Queries one signal at one simulation time.

- preferred mode: `--waveform`
- returns the containing window and the most recent known change at or before the query time

#### `rough-map-chisel`

Adds rough Chisel candidates to a packet.

- joins on `rtl.module_type + rtl.local_signal_name`
- provides candidates only, not exact ownership

### Artifacts And Schema

- refer to `artifact.md`

### Artifacts Directory

Default outputs are stored under:

```text
hardware-debug-waveform/artifacts/
├── authority/<fingerprint>/
├── waveform_meta/<fingerprint>/
└── packets/<fingerprint>/
```

Meaning:

- `authority/`: cache output from `build-authority`
- `waveform_meta/`: metadata cache used by direct `--waveform` queries
- `packets/`: default packet output location suggested by the CLI

`<fingerprint>` is derived from file signatures and key options, so identical inputs usually reuse the same cache directory.

### Limitation

- Direct `query-packet --waveform` may be slow on very large FST files.
- The `main` branch expects `pywellen`; use `no-pywellen` if you want a pywellen-free workflow.
- Without `--rtl-root`, the workflow becomes waveform-only and cannot recover exact RTL ownership.
- `rough-map-chisel` remains heuristic and must not be treated as exact source truth.

## Contribution


This project use [wellen](https://github.com/ekiwi/wellen) to parse vcd/fst waveform
