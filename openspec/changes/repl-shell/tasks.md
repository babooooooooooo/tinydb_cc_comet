# Tasks: repl-shell

> **实施起点**：基于 `tinydb-mvp` 已归档的核心模块（`Database.execute`、`Row`、`Catalog`）实现 REPL 壳层；不修改任何 MVP 模块。
> **TDD 模式**：任务 7.1 / 7.2 采用 TDD（RED → GREEN）；其余任务按实现 → 测试增量验证。
> **预算红线**：`src/tinydb/repl.py ≤ 350 行`；超出需拆分子包（`repl/` 子目录）。

## 1. 骨架与入口

- [x] 1.1 创建 `src/tinydb/repl.py`：模块级 docstring + `def main() -> int:` 入口签名（暂返回 0）
- [x] 1.2 `pyproject.toml` 添加 `[project.scripts]`：`tinydb-repl = "tinydb.repl:main"`
- [x] 1.3 验证安装：`pip install -e ".[dev]"` 后 `tinydb-repl --help` 能找到入口

## 2. 主循环与 SQL 路径

- [x] 2.1 实现 `_make_prompt(db) -> str`：当前数据库路径（`:memory:` 时显示 `:memory:`）
- [x] 2.2 实现 `_read_one_statement() -> str | None`：单行读入；EOF 返回 `None`
- [x] 2.3 实现 `_run_sql(db, sql) -> None`：调用 `db.execute(sql)`，打印 `Row(...)` repr 或 `(no rows)`；捕 `Exception` 打印 `ERROR: <Class>: <msg>` 单行
- [x] 2.4 `main()` 串接 prompt → 读 → execute → 循环；EOF/Ctrl-D 正常退出码 0

## 3. 多行延续

- [x] 3.1 实现 `_is_unterminated(buf: str) -> bool`：扫描 `'` 数为奇 或 `(` > `)` → True
- [x] 3.2 `main()` 改为 buffer 累积：未终止则进入 `...>` 续行 prompt，继续 read；终止则执行并清空 buffer

## 4. 元命令分发

- [x] 4.1 实现 `_handle_meta(line: str, db) -> bool`：行首 `.` 进入；返回 True 表示已处理（不进 SQL 路径），False 表示不是元命令
- [x] 4.2 实现 `.exit` / `.quit`：抛 `_ExitRepl` 内部异常让 main 退出
- [x] 4.3 实现 `.help`：打印元命令清单与快捷键
- [x] 4.4 实现 `.tables`：遍历 `db.catalog.tables` 输出表名（一行一个）
- [x] 4.5 实现 `.schema <name>`：从 `db.catalog.get_table(name)` 读 schema，格式化成 `CREATE TABLE name(c1 T1, c2 T2);` 输出；未知表名报 `ERROR: no such table: <name>`
- [x] 4.6 实现 `.read <path>`：读文件按 `;` 切分，逐条 `_run_sql`；文件不存在报 `ERROR: cannot read file: <path>`

## 5. 历史持久化

- [x] 5.1 实现 `_setup_history() -> None`：尝试 `import readline`；成功则 `read_history_file(os.path.expanduser('~/.tinydb_history'))`（文件不存在不报错）
- [x] 5.2 实现 `_save_history() -> None`：Unix 路径下 `write_history_file`，catch `OSError` 静默（磁盘满 / 权限不足）
- [x] 5.3 Windows ImportError 时静默 fallback；`main()` 跳过 readline 调用，直接用内置 `input()`

## 6. 输出格式

- [x] 6.1 实现 `_format_table(rows: list[Row]) -> str`：列宽 = `min(max(len(h), max(len(str(v)))), 30)`；header + `---` 分隔 + 行；超长值截断加 `…`
- [x] 6.2 在 `_run_sql` 中：若结果是 `list[Row]` 且非空，调用 `_format_table`；空结果显示 `(no rows)`
- [x] 6.3 单条错误格式：`ERROR: ParseError: line 1, col 5: ...`（与现有 exception 形状对齐）

## 7. 测试

- [x] 7.1 `tests/unit/test_repl.py`：覆盖元命令分发、schema 回向格式化、表格化列宽算法、多行未终止判断、history 路径展开
- [x] 7.2 `tests/integration/test_repl_process.py`：`subprocess.Popen(['tinydb-repl', tmp_db])`，stdin 喂 SQL → 读 stdout 断言；至少 4 场景：基本 CRUD、`.tables`、`.read`、EOF 退出码 0

## 8. CLI 旗标

- [x] 8.1 `main(argv: list[str] | None = None)` 支持 `--database <path>` flag；默认 `:memory:`
- [x] 8.2 单元测试：`--database` 解析 + 路径扩展到 `Path` 校验存在性

## 9. 文档

- [x] 9.1 `README.md` 增加 `## REPL` 章节：启动示例（`tinydb-repl` / `tinydb-repl data.db`）、元命令清单、历史文件位置
- [x] 9.2 `examples/repl_smoke.sh`：bash 脚本，`printf` 喂 4 条 SQL 给 `tinydb-repl` 并 grep 关键输出（人眼/CI smoke 用）

## 10. 行数审计与最终自检

- [ ] 10.1 `wc -l src/tinydb/repl.py` ≤ 350；超出则拆 `repl/` 子包
- [ ] 10.2 `pytest --cov=tinydb --cov-report=term-missing --cov-fail-under=85` 全绿，coverage 不退化（保持 ≥ 90%）
- [ ] 10.3 `openspec validate repl-shell --strict` PASS
- [ ] 10.4 `tinydb-repl data.db < smoke.sql` 与手工交互输出一致