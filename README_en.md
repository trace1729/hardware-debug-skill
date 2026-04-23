# Hardware Debug Waveform Skill

## Summary

This skill is a portable hardware-debug workflow for large waveform dumps (`.vcd` or `.fst`): it validates inputs, builds an exact RTL authority table from emitted RTL when available, builds a lightweight waveform metadata cache, and generates compact debug packets that bundle waveform evidence with hierarchy and ownership hints.

## How to use

### Install Skill

```
mkdir -p ~/.codex/skills/
cd ~/.codex/skills
git clone https://github.com/trace1729/hardware-debug-skill.git
```

```
codex
$Hardware Debug Waveform
$Hardware Debug Waveform xxx trigger assert, help me debug with xxx.vcd
$Hardware Debug Waveform xxx trigger assert, help me debug with xxx.fst
$Hardware Debug Waveform explain the module with xxx.vcd
```

### Inputs

The skill expects:

- `--waveform`: path to the waveform file, supporting `.vcd` and `.fst`
- `--scala-root`: path to the Chisel source tree, usually `src/main/scala/xiangshan`

Optional inputs:

- `--rtl-root`: optional but **highly recommand** path to emitted RTL, usually `build/rtl`
- `--error-log` / `--error-info`: optional error-log path, usually `simulator_out.txt`, which can hint `difftest_error` or `assert_error`
- `--focus-scope`: a waveform hierarchy scope to narrow analysis
- `--suggestion`: a human hint such as `hang near dispatch` or `wrong commit behavior`
- `--top`: RTL top module name, default `SimTop`
- `--window-len`: time-window length, default `1000`
Compatibility note:

- `--vcd` still works as an alias for `--waveform`

Recommended standard debug template:

```text
debug_type: hardware bug debug
waveform: /path/to/run.fst_or.vcd
scala-root: /path/to/XiangShan/src/main/scala/xiangshan
rtl-root: /path/to/XiangShan/build/rtl
error-log: /path/to/simulator_out.txt
focus-scope: TOP.SimTop.core.rob
suggestion: what you suspect or what looks wrong
top: SimTop
window-len: 1000
```

If the user only says "debug this" without enough inputs, prefer asking them to fill in this template instead of asking an open-ended free-form question.



### Where Artifacts Are Stored

By default, this skill stores outputs under the skill root:

```text
hardware-debug-waveform/artifacts/
├── authority/<fingerprint>/
├── wave_meta/<fingerprint>/
└── packets/<fingerprint>/
```

The `<fingerprint>` is derived from the input files and key options.

For example:

- authority cache key uses the RTL tree signature and `--top`
- waveform metadata cache key uses the waveform file signature

You can still override the location explicitly with `--out-dir` or `--out`.

---

> below are detail, can ignore

## Exposed Commands

### `inspect-inputs`

Checks inputs and prints the recommended command sequence.

Basic function:

- verifies that the provided paths exist
- if `--error-log` is provided, parses the error log and infers a likely bug type
- if `assert_error` is detected, automatically generates `assert_debug_guide.md` in the same directory
- if `assert_error` is detected, automatically generates `waveform_search_signals.txt` in the same directory
- if `difftest_error` is detected, automatically generates `disassembly.txt` in the same directory
- if `difftest_error` is detected, automatically generates `waveform_search_signals.txt` in the same directory
- prints tree size and waveform size
- warns when preprocessing may be expensive
- supports waveform-only analysis if `--rtl-root` is omitted
- if the target directory is not writable, it explicitly asks for permission instead of silently dropping helper files into another temporary location

### `build-authority`

Builds an exact RTL authority table from emitted RTL.

Basic function:

- parses `.sv` and `.v` files under `--rtl-root`
- extracts modules, declarations, and instance hierarchy
- emits an exact hierarchical signal ownership database

Example:

```bash
python scripts/hw_debug_cli.py build-authority \
  --rtl-root /path/to/build/rtl \
  --top SimTop
```

Cache behavior:

- if a matching cached authority artifact already exists, the command reuses it instead of rebuilding
- add `--force` to rebuild anyway

Important role:

- this step is for persistent exact waveform-to-RTL ownership
- it is not the preferred source for human or LLM reasoning
- after ownership is known, analysis should move to the relevant Scala/Chisel code first

### `build-wave-meta`

Builds a lightweight waveform metadata cache without materializing full per-window change shards.

Basic function:

- parses scope and signal metadata from the waveform
- stores `full_wave_path -> source_id` mapping for direct FST lookup
- writes a small manifest and metadata tables for later direct queries

Example:

```bash
python scripts/hw_debug_cli.py build-wave-meta \
  --waveform /path/to/run.fst
```

When to prefer it:

- point lookups such as "what is this signal at time `t`?"
- targeted direct queries where full waveform preprocessing would be wasteful

### `query-packet`

Builds a compact debug packet for one time range.

Basic function:

- loads waveform metadata and waveform changes for one requested time range
- optionally joins exact RTL ownership from an authority database
- emits a compact packet suitable for LLM analysis

Example with exact RTL:

```bash
python scripts/hw_debug_cli.py query-packet \
  --waveform /path/to/run.fst \
  --authority /tmp/hw_debug_rtl_authority/rtl_authority.sqlite3 \
  --focus-scope TOP.SimTop.core.rob \
  --t-start 123000 \
  --t-end 124000 \
  --out /tmp/hw_packet.json
```

Example in waveform-only mode:

```bash
python scripts/hw_debug_cli.py query-packet \
  --waveform /path/to/run.fst \
  --t-start 123000 \
  --t-end 124000 \
  --out /tmp/hw_packet.json
```

Direct FST example with lightweight metadata cache:

```bash
python scripts/hw_debug_cli.py query-packet \
  --waveform /path/to/run.fst \
  --focus-scope TOP.SimTop.core.rob \
  --t-start 123000 \
  --t-end 124000 \
  --out /tmp/hw_packet.json
```

When to prefer it:

- targeted packet extraction over one suspicious time range
- cases where full waveform preprocessing is too expensive relative to the number of queries

### `query-signal-value`

Queries one signal's value at one simulation time.

Basic function:

- resolves the signal handle from waveform metadata
- asks the FST reader for the value at the requested time
- returns the value if known

Example:

```bash
python scripts/hw_debug_cli.py query-signal-value \
  --waveform /path/to/run.fst \
  --signal TOP.SimTop.core.rob.commit_valid \
  --time 123456
```

### `rough-map-chisel`

Adds rough Chisel candidates to a packet by joining against an external rough mapping artifact.

Basic function:

- reads a packet
- joins on `rtl.module_type + rtl.local_signal_name`
- emits rough Chisel candidates without claiming exact source truth

Example:

```bash
python scripts/hw_debug_cli.py rough-map-chisel \
  --packet /tmp/hw_packet.json \
  --mapping /tmp/rough-mapping.json \
  --out /tmp/hw_packet_rough.json
```

## General Pipeline

The pipeline has three main phases.

### Phase 1: RTL Parsing

This phase is optional, but it provides exact RTL ownership and is the strongest mapping layer.

If `build/rtl` is available, this should be treated as the preferred path because it materially improves mapping accuracy.

However, its main purpose is indexing and ownership recovery, not primary source-level reasoning.

General flow:

1. Recursively discover emitted RTL files under `build/rtl`.
2. Parse module definitions and signal declarations.
3. Build instance hierarchy starting from `--top`.
4. Expand module-local signals into exact hierarchical RTL signal names.
5. Store those results in JSON and SQLite artifacts.

What this phase gives you:

- exact waveform-visible RTL ownership when names match
- module type
- instance path
- local RTL signal name
- source RTL file

How to use that output:

- use it to identify the right module and signal region
- then search the relevant Scala/Chisel source first
- avoid diving into generated SystemVerilog unless Scala leaves an important gap

### Phase 2: Waveform Metadata Cache

This phase is the lightweight waveform indexing stage.

General flow:

1. Parse waveform hierarchy and traced signals.
2. Assign stable internal IDs such as `sigN` and `scopeN`.
3. Record `full_wave_path -> source_id` for direct FST lookup.
4. Build compact metadata files and indexes for quick signal and scope lookup.

What this phase gives you:

- full signal inventory
- full scope inventory
- queryable signal metadata
- a reusable mapping from waveform path to FST handle
- much lower setup cost than full waveform materialization

### Phase 3: Packet Generation

This phase packages only the evidence needed for one debug slice.

General flow:

1. Select one suspicious time range.
2. Resolve the signals for that scope from cached metadata.
3. Read only the requested handles directly from the FST within that time range.
4. Join exact RTL ownership if an authority database is available.
5. Emit a compact JSON packet for LLM consumption.

What this phase gives you:

- time range summary
- touched signals in the selected time range
- exact RTL ownership where available
- unresolved signals where ownership could not be proven

## Main Artifacts And Schemas

### RTL Authority Artifacts

#### `rtl_authority.sqlite3`

Primary exact RTL lookup database.

Table: `authority_lookup`

- `full_signal_name`: exact hierarchical RTL signal name
- `module_type`: emitted RTL module type that owns the signal
- `instance_path`: hierarchical instance path of the owning instance
- `local_signal_name`: signal name local to the module
- `signal_kind`: declaration kind such as wire/reg/port
- `direction`: port direction if applicable
- `decl_width_bits`: declared bit width
- `source_file`: emitted RTL file that declared the signal
- `provenance`: currently `emitted_rtl_exact`

Primary use:

- exact lookup from waveform path to emitted RTL owner

#### `rtl_authority_table.json`

Full JSON export of the authority extraction.

Top-level structure:

- `version`
- `top`
- `rtl_root`
- `summary`
- `signals`
- `coverage_gaps`

`summary` contains:

- `rtl_file_count`
- `module_count`
- `signal_count`
- `cached_module_template_count`

Each row in `signals` contains the same fields as `authority_lookup`.

#### `rtl_authority_index.json`

Dictionary form keyed by exact hierarchical signal name.

Shape:

```json
{
  "SimTop.core.rob.headPtr": {
    "module_type": "...",
    "instance_path": "...",
    "local_signal_name": "...",
    "full_signal_name": "...",
    "signal_kind": "...",
    "direction": "...",
    "decl_width_bits": 8,
    "source_file": "...",
    "provenance": "emitted_rtl_exact"
  }
}
```

Primary use:

- simple JSON-based exact lookup when SQLite is not convenient

### Waveform DB Artifacts

#### `manifest.json`

Entry point for the waveform database.

Top-level structure:

- `version`
- `waveform`
- `summary`
- `tables`

`waveform` contains:

- `path`
- `format`

`summary` contains:

- `signal_count`
- `scope_count`
- `window_count`
- `change_count`

`tables` contains paths to the other artifacts:

- `signals`
- `signal_metadata_db`
- `scopes`
- `scope_signal_index`
- `windows`
- `window_changes_dir`
- `window_index`
- `signal_window_index`

#### `signals.json`

Signal inventory collected from the VCD header.

Each row contains:

- `signal_id`: stable internal ID like `sig123`
- `vcd_id_code`: compact VCD symbol
- `scope_id`: owning scope ID
- `full_wave_path`: full hierarchical waveform path
- `local_name`: local signal name inside its scope
- `bit_width`
- `value_kind`: `scalar` or `vector`

#### `scopes.json`

Hierarchy inventory collected from the VCD header.

Each row contains:

- `scope_id`: stable internal ID like `scope12`
- `full_scope_path`: full hierarchical scope path
- `parent_scope_id`
- `scope_kind`
- `local_name`

#### `scope_signal_index.json`

Scope-to-signal index.

Shape:

```json
{
  "TOP.SimTop.core.rob": ["sig10", "sig11", "sig12"]
}
```

Primary use:

- quickly enumerate which signals belong to a given scope

#### `signal_metadata.sqlite3`

Queryable signal metadata database.

Table: `signal_metadata`

- `signal_id`
- `scope_id`
- `full_scope_path`
- `full_wave_path`
- `local_name`
- `bit_width`
- `value_kind`

Primary use:

- query signal metadata by scope or signal path without loading large JSON files

#### Direct Range Query Behavior

In the hybrid path, waveform changes are not pre-expanded into on-disk time windows.

Primary use:

- resolve metadata once
- read only the requested signals for the requested time range
- avoid materializing large intermediate change shards when only a few focused queries are needed

### Packet Artifacts

#### `packet.json`

Compact debug packet for one query time range.

Top-level structure:

- `version`
- `query`
- `window_summary`
- `focus_signals`
- `notes`

`query` contains:

- `focus_scope`
- `t_start`
- `t_end`

`window_summary` contains:

- `t_start`
- `t_end`
- `change_count`
- `active_signal_count`

Each row in `focus_signals` contains:

- `signal_id`
- `full_wave_path`
- `bit_width`
- `changes`
- `rtl`

Each row in `changes` contains:

- `t`
- `signal_id`
- `value`

`rtl` is either:

- exact:
  - `match_status: exact`
  - `module_type`
  - `source_file`
  - `local_signal_name`
- unresolved:
  - `match_status: unresolved`

`notes` may contain unresolved-count summaries.

#### `rough-join.json`

Packet plus rough Chisel candidates.

Top-level structure:

- `version`
- `packet_path`
- `mapping_path`
- `signals`

Each row in `signals` contains:

- `full_wave_path`
- `rtl`
- `rough_chisel`

`rough_chisel` is either:

- rough:
  - `match_status: rough`
  - `chisel_module`
  - `chisel_path`
  - `rtl_module`
  - `rtl_signal`
  - `notes`
- unresolved:
  - `match_status: unresolved`

## How The LLM Should Use These Artifacts

Recommended order:

1. Run `inspect-inputs`.
2. Build the waveform DB.
3. Build RTL authority if emitted RTL is available.
4. Query a packet for one suspect window.
5. Use `query-signal-value` if you need the value of one signal at one exact time.
6. Read `focus_signals[*].changes` as raw evidence, but summarize the pattern instead of echoing detailed value dumps.
7. Treat `rtl.match_status == exact` as authoritative emitted RTL ownership.
8. Use the matched RTL module and signal names to find the most relevant Scala/Chisel source and analyze that first.
9. If rough Chisel mapping exists, present it only as a candidate, never as proven ownership.
10. Only fall back to SystemVerilog when Scala/Chisel analysis cannot explain the behavior clearly enough.

When writing the final debugging answer, keep artifact discussion very short.

Preferred answer shape:

- one short line on artifact mode, for example `exact RTL mode` or `waveform-only mode`
- then focus mainly on suspected RTL module, a compact summary of the waveform change pattern, and the likely mechanism from Scala/Chisel analysis
- include rough Chisel candidates only as a small follow-up when useful

The final answer should start with a concise summary, followed by a more detailed analysis section that expands the summary.

Use precise terminology:

- for signals and timing, prefer digital-circuit terms such as `rising edge`, `falling edge`, `handshake`, `backpressure`, `stall`, `flush`, `valid`, and `ready`
- for the design and behavior, prefer computer-architecture terms such as `pipeline stage`, `hazard detection`, `forwarding`, `cache hierarchy`, `fetch/decode/execute`, `instruction set architecture`, `bus arbitration`, `memory consistency`, and `commit/retire`

Avoid spending much space on:

- artifact inventories
- file path dumps
- long exact-signal listings
- raw per-cycle value transitions
- long generated SystemVerilog excerpts
- preprocessing mechanics
- schema details

unless the user explicitly asks for those details.

Wording discipline:

- use `exact RTL match` for authority-backed results
- use `waveform-only analysis` when no authority database exists
- use `rough Chisel candidate` for approximate source recovery
- use `unresolved` when the artifact cannot prove the match

## Notes On Performance

- VCD ingestion is the expensive one-time cost.
- Later queries become much cheaper because they operate on window shards and metadata indexes.
- Large artifacts can still produce multi-GB outputs, especially for large waveforms.

## Minimal Example

```bash
cd hardware-debug-waveform

python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /proj/src/main/scala/xiangshan \
  --rtl-root /proj/build/rtl \
  --vcd /proj/run.vcd

python scripts/hw_debug_cli.py build-authority \
  --rtl-root /proj/build/rtl \
  --top SimTop \
  --out-dir /tmp/hw_debug_rtl_authority

python scripts/hw_debug_cli.py build-wave-meta \
  --vcd /proj/run.vcd \
  --out-dir /tmp/hw_wave_meta

python scripts/hw_debug_cli.py query-packet \
  --waveform /proj/run.vcd \
  --authority /tmp/hw_debug_rtl_authority/rtl_authority.sqlite3 \
  --meta-dir /tmp/hw_wave_meta \
  --focus-scope TOP.SimTop.core.rob \
  --t-start 123000 \
  --t-end 124000 \
  --out /tmp/hw_packet.json
```

## Limitations

- Exact Chisel ownership is not proven by this skill.
- Exact mapping stops at emitted RTL unless another artifact proves more.
- Rough Chisel mapping is heuristic and must be labeled clearly.
- Waveform-only mode still works, but exact RTL ownership will be unavailable.
