# Proposal: CLI 功能增强

## Why

`tinydb-repl` 当前是一个 302 行的最小可用 shell，使用 `input()` 单行读取 + 手工 SQL 终止符判定（`;`）拼接多语句。能力局限：
- 无多行编辑能力：编写长查询（多 JOIN、子查询）只能反复回车续行
- 无语法高亮：黑白字符流阅读吃力
- 无行编辑：无法用方向键/Emacs 快捷键（Ctrl-A/E/K 等）修改已输入内容
- 无高级 meta 命令：无法直接查看 SQL 执行计划（`.explain`）、索引元数据、统计信息、计时
- 无输出格式切换：所有结果只能以表格渲染

随着聚合（`tinydb-aggregation`）、JOIN（`2026-07-24-join-query`）、并发控制（`concurrency-control`）等能力上线，REPL 已无法满足开发者调试、运维巡检、benchmark 等使用场景。

## What Changes

新增 `cli-enhancements` capability：

1. **多行编辑**：基于 `prompt_toolkit` 提供真多行输入 + 跨行编辑 + 自动括号闭合
2. **语法高亮**：基于 `pygments` 在 REPL 输入时实时高亮 SQL token（关键字、字符串、数字、运算符、注释）
3. **行编辑**：依赖 `prompt_toolkit` 自带 Emacs/Vi 键绑定；提供历史搜索（↑/↓ Ctrl-R）
4. **新 meta 命令**：
   - `.explain <SQL>`：执行并展示 `LogicalPlan` 树（来自 `plan.format_plan()`）
   - `.indexes [table]`：列出数据库中所有索引（BTree 索引元数据）
   - `.stats`：表/行数/页数/空闲页数/WAL 大小
   - `.timer on|off`：执行计时（毫秒输出在结果下方）
   - `.format csv|json|table`：切换结果输出格式
5. **现有命令增强**：`.help` 自动列出全部 meta 命令与新快捷键
6. **依赖管理**：`pygments>=2.18`、`prompt_toolkit>=3.0` 加入 `pyproject.toml` 的 `dependencies`

## Capabilities

### New Capabilities

- `cli-enhancements` — 多行/高亮/行编辑 + 新 meta 命令 + 输出格式切换

### Modified Capabilities

无

## Impact

- **新增运行时依赖**：`pygments`、`prompt_toolkit`（stdlib 之外的第一个非测试依赖；与 `concurrency-control` 无关）
- **代码迁移**：`repl.py` 由 302 行扩展至 ~700-900 行；考虑拆分为多个模块（`_repl_io.py`、`_repl_meta.py`、`_repl_format.py`）保持单文件 ≤ 800 行
- **CLI 入口兼容**：现有 `tinydb-repl` 入口参数（`--database PATH`）不变；环境变量 `PYTHONSTARTUP` 可加载用户 `.tinydb_init.py`
- **历史文件**：保留现有 `~/.tinydb_history` 路径；prompt_toolkit 的历史格式需配置兼容
- **测试策略**：单元测试（meta 命令解析）+ 集成测试（prompt_toolkit Patch_stdout/Patch_stdin + stdin 注入 SQL 片段）
- **不破坏现有行为**：所有现有 meta 命令（`.exit`/`.quit`/`.help`/`.tables`/`.schema`/`.read`）保持兼容
- **可选降级**：当 `prompt_toolkit` 不可导入时，REPL 退化为单行 `input()` 模式（保持 stdlib-only fallback，行为类似 Task 1 的 `_HAS_FCNTL` 处理）

## Out of Scope

- 远程/网络 REPL（SSH 通道、Web UI）
- SQL 自动补全（候选表/列提示）—— 留作 follow-up
- 持久化配置（`.tinydbrc`）—— 留作 follow-up
- Vi 模式之外的自定义键绑定 —— 留作 follow-up
- `tinydb-repl` 之外的 CLI 子命令（`tinydb dump`、`tinydb import` 等）—— 留作独立 change