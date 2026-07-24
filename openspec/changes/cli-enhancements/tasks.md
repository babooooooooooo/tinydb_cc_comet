# Tasks: CLI 功能增强

## 1. 依赖与构建配置

- [ ] 1.1 在 `pyproject.toml` 的 `[project] dependencies` 中加入 `pygments>=2.18` 和 `prompt_toolkit>=3.0.0`
- [ ] 1.2 运行 `pip install -e .` 验证依赖可解析
- [ ] 1.3 在 `pyproject.toml` 的 `[project.optional-dependencies]` 添加 `repl = ["pygments>=2.18", "prompt_toolkit>=3.0.0"]` 以允许可选安装（推荐做法）

## 2. 输入/输出层 `_repl_io.py`

- [ ] 2.1 创建 `src/tinydb/_repl_io.py`：定义 `ReplIO` 类，封装 `prompt_toolkit.PromptSession` + `PygmentsLexer` + `FileHistory`
- [ ] 2.2 在模块顶层 `try/except ImportError`：当 `prompt_toolkit` 不可导入时，提供 `_FallbackIO`（基于 `input()` 的退化实现）
- [ ] 2.3 实现多行检测：扫描缓冲区中是否有未闭合的引号、括号、`;`；未终止则使用 `CONTINUATION_PROMPT`
- [ ] 2.4 实现历史加载与保存：`~/.tinydb_history`，与现有 readline 路径兼容
- [ ] 2.5 实现 `NO_COLOR` 环境变量与 `TERM=dumb` 检测；不支持时禁用颜色 token

## 3. 结果格式化 `_repl_format.py`

- [ ] 3.1 创建 `src/tinydb/_repl_format.py`：定义 `format_rows(rows: list[Row], fmt: str) -> str` 分发函数
- [ ] 3.2 实现 `table` 格式（迁移现有 `_format_table` 逻辑）
- [ ] 3.3 实现 `csv` 格式（`csv.writer` + `StringIO`，RFC 4180）
- [ ] 3.4 实现 `json` 格式（`json.dumps` 数组；Row 字段名映射）
- [ ] 3.5 添加单元测试 `tests/unit/test_repl_format.py` 覆盖三种格式的快照对比

## 4. meta 命令注册表 `_repl_meta.py`

- [ ] 4.1 创建 `src/tinydb/_repl_meta.py`：定义 `META_COMMANDS: dict[str, Callable]` 与分发函数 `handle_meta(line: str, db: Database) -> bool`
- [ ] 4.2 迁移现有 meta 命令（`.exit` / `.quit` / `.help` / `.tables` / `.schema` / `.read`）到注册表
- [ ] 4.3 实现 `.explain <sql>`：调用 `db.explain_plan(sql)` + `plan.format_plan(plan)` 渲染
- [ ] 4.4 实现 `.indexes [table]`：遍历 `catalog.indexes` + `IndexManager` 输出 BTree 元数据
- [ ] 4.5 实现 `.stats`：聚合表数/行数/页数/空闲页数/WAL 大小
- [ ] 4.6 实现 `.timer on|off`：切换模块级 `_TIMER_ENABLED`
- [ ] 4.7 实现 `.format <table|csv|json>`：切换模块级 `_OUTPUT_FORMAT`
- [ ] 4.8 实现 `.color on|off`：切换 ANSI 颜色输出
- [ ] 4.9 更新 `.help` 输出以反映所有新命令

## 5. 整合与 REPL 主循环

- [ ] 5.1 重构 `src/tinydb/repl.py`：将 main / `_interactive_loop` 瘦身；调用 `_repl_io` / `_repl_meta` / `_repl_format`
- [ ] 5.2 整合计时：`.timer on` 时，在 `_run_sql` 结果后追加 `Time: X.XXX ms`
- [ ] 5.3 整合输出格式：`.format` 切换后，下次查询按新格式输出
- [ ] 5.4 在 `repl.py` 入口处输出首次启动提示（"`.help` for commands, `.timer on` for timing"）

## 6. 集成测试

- [ ] 6.1 创建 `tests/integration/test_repl_io_prompt_toolkit.py`：使用 `PromptSession` Patch + stdin 注入 SQL 片段；断言执行成功
- [ ] 6.2 创建 `tests/integration/test_repl_multiline.py`：跨 5 行 SELECT 查询
- [ ] 6.3 创建 `tests/integration/test_repl_color_off.py`：`NO_COLOR=1` 环境下不输出 ANSI 颜色码
- [ ] 6.4 创建 `tests/integration/test_repl_fallback.py`：monkey-patch `prompt_toolkit` 不可导入；REPL 退化为 `input()` 模式
- [ ] 6.5 创建 `tests/integration/test_repl_meta_commands.py`：所有 meta 命令的端到端行为

## 7. 文档

- [ ] 7.1 更新 `README.md`：新增 "REPL" 章节，列出多行/高亮/行编辑与新 meta 命令
- [ ] 7.2 在 `docs/superpowers/specs/` 下新建 `cli-enhancements.md`，汇总 spec 中的公开契约
- [ ] 7.3 更新 `CHANGELOG.md`：新增 `cli-enhancements` 条目

## 8. 最终验证

- [ ] 8.1 运行 `pytest` 完整套件，确认通过且覆盖率 ≥ 92%（保持基线）
- [ ] 8.2 手动冒烟：启动 `tinydb-repl --database /tmp/test.db`，验证多行 / 高亮 / 行编辑 / 新 meta 命令
- [ ] 8.3 验证回退路径：临时移除 `prompt_toolkit` 包，确认 REPL 仍可用（退化模式）