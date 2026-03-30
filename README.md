# Hardware Debug Waveform Skill

## 总结

该 skill 面向硬件波形调试场景，结合波形(fst/vcd)、emitted RTL 与 Scala/Chisel 源码，为 LLM 提供结构化调试入口。

优先使用 `wellen` 库处理波形

- 直接从波形文件查询单个信号在指定时刻的值
- 直接从波形文件抽取指定时间窗的 debug packet
- 在提供 emitted RTL 时，补充精确的 RTL ownership
- 以 Scala/Chisel 源码作为最终分析与定位的主要依据

`build-wave-db` 仍然保留，但定位为备用路径，适合重复查询、离线缓存或需要显式构建 artifact 的场景。

格式说明：

- 直接 `--waveform` 查询路径依赖 `wellen`，支持 `pywellen` 可读取的格式，包括 VCD 与 FST
- `build-wave-db` 备用路径目前仍按 VCD 预处理设计，不应将其视为 FST 的有效路径

## 如何使用

### 安装

```bash
pip install pywellen
mkdir -p ~/.codex/skills/
cd ~/.codex/skills
git clone https://github.com/trace1729/hardware-debug-skill.git hardware-debug-waveform
```

### 简单使用

```text
codex
$Hardware Debug Waveform help me debug xxx.vcd/fst
$Hardware Debug Waveform explain this module with xxx.vcd/fst
```

必要输入：

- `--scala-root`：Scala/Chisel 源码树路径
- `--waveform`：用于 `inspect-inputs` 与 `build-wave-db` 的波形路径

常用可选输入：

- `--rtl-root`：**推荐** emitted RTL 根目录，通常为 `build/rtl`
- `--focus-scope`：希望聚焦的层级 scope
- `--suggestion`：人工调试提示
- `--top`：RTL 顶层模块名，默认 `SimTop`
- `--window-len`：时间窗长度，默认 `1000`


## 基本流水线

### LLM 在底层如何使用这些产物

1. `inspect-inputs` 校验输入路径、估计产物规模，并打印推荐命令。
2. 若提供 emitted RTL，则通过 `build-authority` 生成精确的 RTL ownership 数据库。
3. 优先使用 `query-packet --waveform` 或 `query-signal-value --waveform`，由 `wellen` 直接读取波形文件。
4. 生成的 packet 只保留当前分析时间窗中真正相关的信号变化，并可附带 exact RTL ownership。
5. LLM 再根据 `module_type`、`local_signal_name`、`focus_scope` 等线索回到 Scala/Chisel 源码中做根因分析。
6. `build-wave-db` 仅在需要缓存、重复查询或显式保存规范化波形 artifact 时使用。

对 FST 而言，应只使用直接 `--waveform` 查询路径。

## 详细分析

### 子命令说明

#### `inspect-inputs`

用于检查输入并打印推荐的后续命令。

- 检查 `--scala-root`、`--waveform`、`--rtl-root` 是否存在
- 估算波形与源码树规模
- 输出默认 artifact 路径
- 优先打印直接波形查询命令
- 保留 `build-wave-db` 作为备用路径

#### `build-authority`

从 emitted RTL 构建精确的 RTL authority。

- 解析 `.sv` 与 `.v`
- 建立实例层级
- 展开层级化信号名
- 输出 JSON 与 SQLite 查询产物

#### `query-packet`

生成单个时间窗的 debug packet。

- 推荐模式：`--waveform`
- 备用模式：`--manifest`
- 可选关联 `--authority`
- 可选使用 `--focus-scope` 缩小范围
- 对大型 FST，直接 packet 查询可能较慢

#### `query-signal-value`

查询单个信号在指定仿真时刻的值。

- 推荐模式：`--waveform`
- 备用模式：`--manifest`
- 返回目标时刻所在窗口，以及该时刻之前最近一次已知变更

#### `build-wave-db`

将 VCD 规范化为可落盘查询的波形数据库。

- 当前定位为备用路径
- 适合重复查询、缓存复用或离线产物保存
- 当前实现仍以 VCD 预处理为主
- 当前不应作为 FST 的有效入口

#### `rough-map-chisel`

将外部 rough mapping 结果补充到 packet 中。

- 通过 `rtl.module_type + rtl.local_signal_name` 做粗略 join
- 只提供候选，不宣称为精确来源

### 产物与 Schema

#### 主要产物

`rtl_authority.sqlite3`

表：`authority_lookup`

关键字段：
- `full_signal_name`
- `module_type`
- `instance_path`
- `local_signal_name`
- `source_file`

`manifest.json`

备用波形数据库入口。

顶层字段：
- `version`
- `waveform`
- `summary`
- `tables`

`packet.json`

单次时间窗查询产物。

顶层字段：
- `version`
- `query`
- `window_summary`
- `focus_signals`
- `notes`

`query-signal-value` 输出

顶层字段：
- `version`
- `query`
- `signal`
- `window`
- `value_at_time`

### Artifacts 目录

默认输出位于：

```text
hardware-debug-waveform/artifacts/
├── authority/<fingerprint>/
├── wave_db/<fingerprint>/
└── packets/<fingerprint>/
```

说明如下：

- `authority/`：`build-authority` 的缓存输出
- `wave_db/`：`build-wave-db` 的缓存输出
- `packets/`：CLI 推荐的 packet 默认输出位置

`<fingerprint>` 基于输入文件签名与关键参数生成，因此相同输入通常会复用同一缓存目录。

### 限制

- 当前推荐路径虽然依赖 `wellen`，但 CLI 仍保留历史兼容别名 `--vcd`。
- 直接 `--waveform` 查询路径支持 VCD 与 FST。
- `build-wave-db` 目前仍是以 VCD 为中心的预处理实现，并未替换为 `wellen` 全量落盘，也不应视为 FST 支持路径。
- 直接 `query-packet --waveform` 适合按需查询；若需要大量重复窗口查询，预构建 wave DB 仍可能更合适。
- 对非常大的 FST，`query-packet --waveform` 可能较慢。
- 未提供 `--rtl-root` 时，只能做 waveform-only 分析，无法恢复 exact RTL ownership。
- `rough-map-chisel` 仅提供粗略候选，不能作为精确来源依据。
