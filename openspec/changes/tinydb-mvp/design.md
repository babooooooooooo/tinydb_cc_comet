# Design: tinydb-mvp

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

`tinydb` 是一个从零构建的 Python 嵌入式关系型数据库（项目愿景见仓库根目录 `tinydb-proposal.md`）。MVP 阶段是这个项目的"第一个里程碑切片"，目标是交付一个能端到端跑通最小 SQL 子集的可教学存储引擎。本设计解决两个核心问题：

1. 用什么样的文件 / 页 / 行编码结构，让存储层既"足够真实"又能用纯 Python 在 ~150-250 行内表达清楚？
2. SQL 解析器、executor、storage 三层如何解耦，才能让每层都能被独立教学？

约束：
- 纯 Python 实现，零运行时依赖
- 单文件持久化，单进程单线程
- 不与 SQLite 拼性能或兼容性
- 每个模块行数预算上限（已在 proposal 中声明）必须被尊重，违规即返工

## Goals / Non-Goals

**Goals：**
- 端到端打通 `CREATE TABLE → INSERT → SELECT WHERE col = x → DELETE`
- 模块边界清晰，每个模块可被一名中高级 Python 开发者 30 分钟内读完
- 4 层测试金字塔（unit / integration / e2e / property）覆盖核心模块 ≥85%
- 文件格式与 module 名稳定到足以承载后续 `tinydb-acid`（WAL 接入）和 `tinydb-engine-v2`（SQL/索引扩展）

**Non-Goals（本期明确不做）：**
- ACID / WAL / 崩溃恢复（→ `tinydb-acid`）
- UPDATE、WHERE AND/OR/IN/LIKE、ORDER BY、LIMIT、聚合、JOIN（→ `tinydb-engine-v2`）
- 索引、列约束、扩展类型、CLI（→ `tinydb-engine-v2`）
- 并发安全（永久 out）
- 性能基准（学习项目不参与 SQLite 同维度比较）

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       Python API 层                              │
│   tinydb.Database(file).execute(sql_str) → list[Row]            │
│   tinydb.Database(path) / tinydb.Database(':memory:')           │
└─────────────────────────────┬───────────────────────────────────┘
                              │ SQL 字符串
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SQL 解析管线                                  │
│   tokenizer.tokenize(sql) → list[Token]                          │
│   parser.parse(tokens) → ASTNode                                │
│        ├─ CreateTable / DropTable (DDL)                          │
│        ├─ Insert / Select / Delete (DML)                         │
│        └─ ParseError(line, col, message)                         │
└─────────────────────────────┬───────────────────────────────────┘
                              │ AST
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                          Executor                                │
│   Executor(pager, catalog).run(stmt) → list[Row]                │
│        ├─ DDL: catalog.{create,drop}_table                       │
│        └─ DML: scan / filter / project / mutate                 │
└─────────────────────────────┬───────────────────────────────────┘
                              │ row reads/writes
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Storage 引擎                                │
│                                                                  │
│   ┌─────────── File (.db) ──────────────────────────────────┐   │
│   │ Page 0  │ Page 1    │ Page 2    │ Page 3    │ Page 4+  │   │
│   │ header  │ catalog   │ table A   │ table A   │ 空闲     │   │
│   │(magic,  │(tables[], │(slotted   │(slotted   │          │   │
│   │ version)│ root_pg)  │ rows)     │ rows)     │          │   │
│   └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**层职责边界**：
- **API 层**：对外接口，不感知 SQL 内部表示
- **解析器**：纯函数，不持有状态、不调 I/O
- **Executor**：唯一同时持有 `Pager` + `Catalog` 的层；所有 I/O 在这里发生
- **Pager**：单文件 mmap，每次操作拉一页进内存，对外暴露 `read_page(id)` / `write_page(id)` / `alloc_page()`
- **Catalog**：表名 → (root_page_id, schema) 映射，序列化为 Page 1

## Decisions

### D1. 单文件 + 固定 4KB 页 + mmap

**选择**：用 `mmap` 打开 .db 文件，按 4KB 分页寻址；page 0 = 文件头（magic + schema_version），page 1 = catalog，其余页 = 表数据。

**为什么不是 SQLite 风格的可变 page size**：可变 page size 在 MVP 阶段会引入额外的元数据开销（每个 page 自己存自己的 size），而固定 4KB 用一个全局常量 + 简单 mod 切分，复杂度归零。性能差异在 MVP 不重要。

**为什么不是 `pickle` / `json` / `csv` 等高层序列化**：它们都把"整张表"或"整个数据集"读进内存反序列化，无法支持后续的 slotted page / B-tree / 增量更新。MV 层从一开始就必须给后续 change 留接口。

### D2. Slotted Pages（参考 SQLite 简化版）

**选择**：每页结构 = `page_header(num_slots, free_space_offset, table_id)` + `slot_directory[offset: u16, length: u16]` + `data_area` + `free_space` 末尾。

**为什么不用 append-only 行存储**：append-only 没有原位更新的能力，对 UPDATE 友好但 DELETE 会留下碎片；slotted pages 用 tombstone + slot 复用的方式既保留 UPDATE 友好又控制复杂度。SQLite 选 slotted pages 不是偶然。

**行编码**：`null_bitmap[ceil(col_count/8)]` + `length_prefixed_values`（每列 2 字节变长前缀）；固定类型如 INT 用定长槽，避免每行解析的开销。

### D3. 解析器：recursive descent，无错误恢复

**选择**：手写 tokenizer + recursive descent parser，每个语法规则一个方法。语法错误直接 `raise ParseError(line, col, "expected X, got Y")`，不做 recovery。

**为什么不用 PLY / Lark 等 parser generator**：parser generator 引入 build-time 依赖（违反零运行时依赖不是问题，但 IDE 支持 + 调试体验明显不利），且生成的代码可读性反而不如手写版本。手写版的核心语句类型只有 5 个，~600 行可承受。

**为什么不做错误恢复**：recovery 让代码量翻倍且没有教学收益；MVP 阶段把"报错清晰"做到位即可。

### D4. Catalog 单页实现

**选择**：page 1 = catalog 页，存 `{table_name: (root_page_id, schema_definition)}`。MVP 阶段 catalog 永远 1 页（表数量受 4KB / 单条 catalog entry 大小限制，估算可容纳 ~50 张表）。

**为什么不做 B-tree catalog**：MVP 阶段的表数量假设是教学 demo 级（< 50 张），单页 catalog 足够。B-tree 是 tinydb-engine-v2 的范围。

### D5. 类型系统：strict mode，无隐式转换

**选择**：所有 insert / where 比较都走 strict 类型校验；`'123'` 不自动转 `123`，`1.0` 不自动转 `1`。

**为什么 strict**：隐式转换语义多到能写一篇 RFC，且 bug 几乎都发生在隐式转换路径上。MVP 阶段 strict + 一份清晰的错误消息 = 学习价值更高。

**例外**：在 WHERE 中允许 `WHERE col = literal`，literal 类型必须与列类型严格一致，否则 executor raise `TypeError`。

### D6. Zero-dep + pytest + hypothesis

**选择**：
- 运行时：纯 stdlib（`mmap` / `struct` / `dataclasses`）
- dev 依赖：`pytest`（主测试）、`hypothesis`（仅 storage 模块的属性测试）

**为什么不用 poetry / pdm**：MVP 阶段的依赖图只有 2 个 dev 包，`pip install -e ".[dev]"` 足够；引入包管理器是 education 反模式（你要学的是数据库，不是 Python 生态）

## Risks / Trade-offs

- **R1：mmap + 进程崩溃数据丢失** → MVP 明确无 fsync 语义；存活的 demo 都是控制良好的本地进程。Mitigation：在文档与 `Database.__init__` docstring 双重声明"非 ACID，崩溃丢数据"。`tinydb-acid` 引入 WAL 时彻底重写 Pager 接口，但 API 层保持兼容
- **R2：固定 4KB 页对超大行不友好** → 一行超过 4KB 的 demo 极少；education 场景不需要；超出行可分多页 spill（但 MVP 先 raise `RowTooLargeError`）
- **R3：单页 catalog 限制表数 < 50** → MVP 阶段明示用户量；溢出时 raise `CatalogFullError` 给出清晰提示
- **R4：解析器无错误恢复** → 用户写错 SQL 时一次性报错；体验确实不如 psql，但代码量翻倍与教学价值不匹配
- **R5：strict mode 拒绝 `WHERE id = '5'`（哪怕 id INT）** → 与 SQL 标准不符，但与 SQLite STRICT TABLE 类似；用户在 README 的"已知限制"段落看到明确说明
- **R6：linear scan only** → 1MB 表查询 100ms 量级，对 demo 足够；性能在 `tinydb-engine-v2` 引入 B-tree 时解决

## Migration Plan

不适用（项目从零起步，无既有代码迁移）。

## Open Questions

- **Q1**：catalog 单页记录格式选择 JSON-like 还是二进制？目前倾向 JSON（可读、库足够小），但 MVP 阶段可在 design 完成前再敲定
- **Q2**：FLOAT 拒绝 inf/NaN 是 raise `ValueError` 还是 silently store NULL？倾向 raise，符合 strict mode 风格
- **Q3**：是否在 MVP 引入 `Row.__repr__` 自动格式化（`Row(id=1, name='alice')`）？倾向引入，对教学和调试都极有帮助
- **Q4**：`Database.execute` 返回 `list[Row]` 对 DDL/DML 返回 `[]` 是否会让用户困惑？备选：DDL 返回 `None`，DML 返回受影响行数；MVP 先返回统一 `list[Row]`（DDL 时为 `[]`），避免接口分裂

上述 Q1-Q4 都在 MVP 的 design/build 阶段可以快速敲定，不会阻塞任务拆分。
