# Waveform Analysis Skill 

## 总结

使用 wellen 预处理波形文件，基于build/rtl 构建 chisel -> verilog 信号映射，从而让LLM更好的根据波形调试。
> 优先使用 `wellen` 库处理波形, 也可以使用基于正则匹配的vcd解析器，这种方法会构建离线缓存，适合重复查询、离线缓存或需要显式构建产物的场景。

## 如何使用

### 安装

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

说明：

- skill 会优先使用当前 Python 环境中的 `pywellen`
- 若当前环境缺失 `pywellen`，会自动尝试使用 skill 内置的 `wellen/pywellen`

### 简单使用

```text
codex
$Hardware Debug Waveform help me debug xxx.vcd/fst
$Hardware Debug Waveform explain this module with xxx.vcd/fst
$Hardware Debug Waveform avoid pywellen, explain this module with xxx.vcd
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
-  `--avoid pywellen`: 使用fallback 方式处理 vcd 文件，更节省 token


## 基本流水线

### LLM 在底层如何使用这些产物

1. `inspect-inputs` 校验输入路径、估计产物规模，并打印推荐命令。
2. 若提供 emitted RTL，则通过 `build-authority` 生成精确的 RTL ownership 数据库。
3. 优先使用 `query-packet --waveform` 或 `query-signal-value --waveform`，由 `wellen` 直接读取波形文件。
4. 生成的 packet 只保留当前分析时间窗中真正相关的信号变化，并可附带 exact RTL ownership。
5. LLM 再根据 `module_type`、`local_signal_name`、`focus_scope` 等线索回到 Scala/Chisel 源码中做根因分析。
6. 查询会复用 `artifacts/waveform_meta/` 下的元数据缓存；`build-wave-db` 仅在显示指定的情况下才可以使用。


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

- 见 `artifact.md`

### Artifacts 目录

默认输出位于：

```text
hardware-debug-waveform/artifacts/
├── authority/<fingerprint>/
├── waveform_meta/<fingerprint>/
├── wave_db/<fingerprint>/
└── packets/<fingerprint>/
```

说明如下：

- `authority/`：`build-authority` 的缓存输出
- `waveform_meta/`：直接 `--waveform` 查询路径的元数据缓存
- `wave_db/`：`build-wave-db` 的缓存输出
- `packets/`：CLI 推荐的 packet 默认输出位置

`<fingerprint>` 基于输入文件签名与关键参数生成，因此相同输入通常会复用同一缓存目录。

### 限制

- 直接 `query-packet --waveform` 适合按需查询；若需要大量重复窗口查询，预构建 wave DB 仍可能更合适。
- 对非常大的 FST，`query-packet --waveform` 可能较慢。
- 未提供 `--rtl-root` 时，只能做 waveform-only 分析，无法恢复 exact RTL ownership。
- `rough-map-chisel` 仅提供粗略候选，不能作为精确来源依据。

### 鸣谢

本项目基于 [wellen](https://github.com/ekiwi/wellen)
