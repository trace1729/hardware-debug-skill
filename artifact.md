
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

### Packet Artifacts

### Direct Query Cache Artifacts

#### `waveform_meta/<fingerprint>/`

Level-1 cache for direct `wellen` queries.

Contains:

- `signals.json`
- `scopes.json`
- `scope_signal_index.json`
- `cache_meta.json`

Primary use:

- avoid rebuilding waveform hierarchy metadata for repeated direct queries

#### `waveform_query/<fingerprint>/`

Level-2 cache for direct query results.

Contains:

- `result.json`
- `cache_meta.json`

`cache_meta.json` records the waveform file signature and the query key, such as:

- signal path + simulation time for `query-signal-value`
- window ID + window length + focus scope + authority identity for `query-packet`

Primary use:

- avoid reopening and requerying the waveform when the same direct query is repeated

#### `packet.json`

Compact debug packet for one query window.

Top-level structure:

- `version`
- `query`
- `window_summary`
- `focus_signals`
- `notes`

`query` contains:

- `window_id`
- `focus_scope`

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
- `window_id`
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
