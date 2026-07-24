# Design: CLI 功能增强（高层架构）

> 本文件给出 cli-enhancements change 的高层架构决策。深度技术细化（API 形状、边界条件、测试策略）将在 Design Doc（design 阶段）中给出。

## 架构总览

```
                       ┌─────────────────────────────────┐
                       │        tinydb-repl 入口          │
                       │     src/tinydb/repl.py (main)   │
                       └────────────────┬────────────────┘
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  ▼                     ▼                     ▼
        ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
        │   _repl_io.py    │  │  _repl_meta.py   │  │  _repl_format.py │
        │  (输入/输出层)    │  │   (meta 命令)    │  │  (结果格式化)    │
        │ prompt_toolkit   │  │ .explain/.indexes│  │ table/csv/json   │
        │ pygments lexer   │  │ .stats/.timer/   │  │                  │
        │ fallback: input()│  │  .format/.help   │  │                  │
        └──────────────────┘  └──────────────────┘  └──────────────────┘
                                        │
                                        ▼
                            ┌──────────────────────┐
                            │   Database (现有)    │
                            │   plan.py (现有)     │
                            └──────────────────────┘
```

## 关键决策（D1-D8）

### D1. 输入层技术选型

| 选项 | 优劣 |
|------|------|
| A. `prompt_toolkit` (Recommended) | 完整多行/行编辑/历史/语法高亮生态；标准选择；引入新依赖 |
| B. 自研 `readline` + `pygments` | 零新依赖；但多行编辑体验差 |
| C. `pyrepl` | 纯 Python；少用；社区支持弱 |

**决策**：选 A `prompt_toolkit>=3.0`，通过 `try/except ImportError` 提供 stdlib `input()` fallback（与 `_HAS_FCNTL` 处理对称）

### D2. 语法高亮实现

基于 `pygments.lexers.sql.SqlLexer` + `prompt_toolkit.lexers.PygmentsLexer` 桥接。

- 实时高亮（每个 keystroke 后重渲染）
- 自定义 token 颜色：仅在终端支持 256 色时启用（`TERM != "dumb"` 检测）
- 关闭方式：`NO_COLOR=1` 环境变量 / `.color off` meta 命令

### D3. meta 命令分发

统一命令注册表：

```python
META_COMMANDS: dict[str, Callable[[list[str], Database], None]] = {
    "explain": _cmd_explain,
    "indexes": _cmd_indexes,
    "stats": _cmd_stats,
    "timer": _cmd_timer,
    "format": _cmd_format,
    # ... 现有 .exit / .help / .tables / .schema / .read
}
```

`._handle_meta()` 改为查表分发；新命令只需注册。

### D4. `.explain` 输出

调用 `db.explain_plan(sql)` 获取 `LogicalPlan`；用 `plan.format_plan(plan)` 渲染为缩进树；附 EXPLAIN 头部信息（estimated cost、table count、index usage）。

**注意**：`.explain` 不执行查询（避免误操作）；若需执行 + 计划，使用 `.explain ANALYZE`（可选，out of scope for v1）

### D5. `.indexes` 输出

遍历 `Catalog.indexes` + `IndexManager` 状态；按 `table.column` 列出 BTree root_page_id、key 数量估算、唯一性。

### D6. `.stats` 输出

聚合：
- 表数量（`len(catalog.tables)`）
- 行总数（`SELECT COUNT(*) FROM _tables` 或遍历 catalog）
- 页数（`pager.page_count()`）
- 空闲页数（`pager.free_list_length()`）
- WAL 大小（`os.path.getsize(wal_path)`）

### D7. `.timer` 行为

- 开启后：`db.execute(sql)` 后追加 `Time: X.XXX ms` 行
- 关闭：默认
- 状态保存在模块级 `_TIMER_ENABLED: bool = False`

### D8. `.format` 支持的输出格式

| 格式 | 说明 |
|------|------|
| `table` (默认) | ASCII 表格（与现有 `_format_table` 相同） |
| `csv`  | RFC 4180 CSV（`csv.writer` + `StringIO`） |
| `json` | JSON 数组（每行一个对象） |

输出格式状态保存在模块级 `_OUTPUT_FORMAT: str = "table"`

## 数据流（典型用例）

### 用例 1：多行 JOIN 查询

```
tinydb> SELECT *
...> FROM users u
...> JOIN orders o ON u.id = o.user_id
...> WHERE u.age > 18
...> ;
```

输入流：
1. 用户按回车 → `_repl_io` 检测缓冲区未终止（无 `;` 结尾）→ 切换 CONTINUATION prompt
2. 用户继续输入 → pygments 实时高亮
3. 用户输入 `;` → 检测语句结束 → 提交给 `Database.execute()`
4. 结果按当前 `.format` 渲染

### 用例 2：`.explain SELECT * FROM users WHERE age > 18`

```
tinydb> .explain SELECT * FROM users WHERE age > 18
Plan:
  Scan(users)
    Filter: age > 18
      Project: *
```

1. 解析为 `.explain <sql>`
2. 调用 `db.explain_plan(sql)` 返回 `LogicalPlan`
3. 用 `plan.format_plan(plan)` 渲染
4. 输出到 stdout

## 模块拆分

`repl.py` 当前 302 行；扩展后预计 ~900+ 行，违反单文件 ≤ 800 行的项目规范（参见 CLAUDE.md）。提前拆分：

```
src/tinydb/repl.py             [KEEP] main + 入口 (~150 行)
src/tinydb/_repl_io.py         [NEW] 输入/输出层 + prompt_toolkit fallback (~250 行)
src/tinydb/_repl_meta.py       [NEW] meta 命令注册表 + 各命令实现 (~300 行)
src/tinydb/_repl_format.py     [NEW] 输出格式化 (table/csv/json) (~150 行)
tests/unit/test_repl_meta.py   [NEW] meta 命令分发测试
tests/unit/test_repl_format.py [NEW] 格式化测试
tests/integration/test_repl_io.py [NEW] prompt_toolkit 端到端测试（fallback 也覆盖）
```

## 测试策略

### 单元测试

| 文件 | 用例 |
|------|------|
| `test_repl_meta.py` | 8 命令解析 + dispatch + 各命令基本行为（用 mock Database） |
| `test_repl_format.py` | table/csv/json 三种格式输出快照对比 |
| `test_repl_io_fallback.py` | monkey-patch `prompt_toolkit` 不可导入时，REPL 退化为 `input()` 模式 |

### 集成测试

| 文件 | 用例 |
|------|------|
| `test_repl_io_prompt_toolkit.py` | 用 `prompt_toolkit.PromptSession` Patch 注入 SQL 片段；断言执行成功 + 高亮 token 正确 |
| `test_repl_multiline.py` | 输入跨 5 行查询；断言拼接 + 执行成功 |
| `test_repl_color_off.py` | `NO_COLOR=1` 环境下不输出 ANSI 颜色码 |

### 手动冒烟

- 启动 `tinydb-repl --database /tmp/test.db`
- 多行 SELECT 验证
- `.explain` / `.indexes` / `.stats` / `.timer on` / `.format csv`
- 高亮颜色正常
- `Ctrl-C` 清空缓冲区
- `Ctrl-D` 退出

## 依赖

`pyproject.toml`：

```toml
dependencies = [
    "pygments>=2.18",
    "prompt_toolkit>=3.0.0",
]
```

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `prompt_toolkit` 与现有 `input()` 行为差异 | 提供 stdlib fallback；新行为在 prompt_toolkit 可用时启用 |
| 终端不支持颜色 / TERM=dumb | 检测 `os.environ.get("TERM")`；不支持时关闭高亮 |
| 跨平台键绑定差异 | 仅 Linux/macOS 测试；Windows 留作 follow-up |
| `.explain` 对无效 SQL 抛异常 | 在命令中捕获 `ParseError`/`TokenizerError` 并友好展示 |
| pygments lexer 对 tinydb 方言支持有限 | 仅高亮标准 SQL token；tinydb 方言扩展留作 follow-up |

## Open Questions（Q1-Q4）

- **Q1**：`.explain ANALYZE`（执行并展示真实耗时）是否在 v1？—— 建议推迟到 v2
- **Q2**：是否支持 `.format` 的 `markdown` / `html`？—— 建议推迟，v1 仅 table/csv/json
- **Q3**：历史文件与 prompt_toolkit 格式互转？—— 通过 prompt_toolkit 的 `FileHistory` 直接接管；旧 readline 历史不再读
- **Q4**：是否在 REPL 启动时自动 `.timer on`？—— 默认 off；首次启动打印提示

## 迁移

无 schema 迁移。新增依赖 + 新增 meta 命令；现有 REPL 用户在升级后获得多行/高亮/行编辑能力，无需额外配置。

回滚策略：移除 `prompt_toolkit` / `pygments` 依赖即可退回 302 行最小 REPL。