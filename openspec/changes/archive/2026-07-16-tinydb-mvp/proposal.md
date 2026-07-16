# Proposal: tinydb-mvp

> **范围声明**：本 change 是 `tinydb` 项目的**第一个里程碑（最小可演示版）**，是后续 `tinydb-acid`（事务）与 `tinydb-engine-v2`（SQL/索引/CLI 扩展）的基石。完整愿景参见仓库根目录的 `tinydb-proposal.md`。

## Why

要从零构建一个可教学的 Python 嵌入式关系型数据库，第一阶段必须交付一个**能跑通端到端最小路径**的可演示版本：纯 Python、单文件持久化、最简 SQL 子集、Python API 直调。如果第一阶段就追求 7 大能力齐备，代码会膨胀到无法教学拆解；如果第一阶段只做底层存储不做 SQL，则无法验证"存储 + 解析 + 执行"三层拼装的正确性。MVP 是两端之间的最小可用切片，能在三周内交付并被一名中高级 Python 开发者 30 分钟内读完核心模块。

## What Changes

- **新增** 单文件页式存储引擎（slotted pages），支持页头 / 槽位 / 空闲空间回收 / 文件魔术字
- **新增** 基础类型系统（INT/TEXT/FLOAT/BOOL），含类型校验、字面量解析、Python ↔ DB 类型转换
- **新增** 最小 SQL 解析器，覆盖 5 个语句：`CREATE TABLE` / `DROP TABLE` / `INSERT INTO` / `SELECT ... FROM` / `DELETE FROM`，WHERE 暂只支持 `col = literal`（无 AND/OR、无 UPDATE、无子查询）
- **新增** 执行器：DDL 创建/删除表；DML 插入 / 全表扫描 / 等值过滤 / 标记删除
- **新增** Python API：`Database(path).execute(sql) -> list[Row]`，库入口与 `tinydb` 顶层包名
- **新增** 端到端 SQL golden 测试集（`tests/e2e/sql/`），纯 Python + pytest，不引入 sqlite3 验证
- **新增** 文档：README + 模块导览（指出每个模块的预期行数上限）
- **暂不引入** WAL、UPDATE、AND/OR 复合条件、ORDER BY/LIMIT、聚合、B-tree、索引、CLI、扩展类型——这些都在后续两个 change 的范围内

## Capabilities

### New Capabilities

- `type-system-basic`：4 个基础类型（INT/TEXT/FLOAT/BOOL）的字面量解析、存储编码、解码校验、Python ↔ DB 双向转换；严格类型（不允许隐式转换）；FLOAT 拒绝 inf/NaN
- `storage-engine`：单文件持久化，slotted pages（每页 = header + slots 数组 + free space），文件头魔术字，固定页大小（4KB），表元数据页（catalog），全表扫描 + 等值定位 API
- `sql-minimal-parser`：tokenizer（识别关键字 / 标识符 / 字面量 / 运算符 / 括号 / 分号）+ recursive descent parser，输出 AST，覆盖 5 个 DDL/DML 语句；解析错误抛出带行号的 `ParseError`
- `python-api`：`tinydb` 顶层包入口；`Database` 类（接受 file 路径或 `:memory:`）；`execute(sql) -> list[Row]`；`Row` 支持按列名访问

### Modified Capabilities

无（项目从零开始，`openspec/specs/` 当前为空）

## Impact

- 新增 Python 包 `tinydb`，零运行时依赖
- 单一 `.db` 文件作为数据存储格式（首个 MVP 版）
- 新增 dev 依赖：`pytest`（核心）、`hypothesis`（属性测试，仅在 storage 模块涉及）
- 公共接口仅 `tinydb.Database` 一类；后续 change 会扩展但不破坏 MVP API 形状
- 不影响外部系统（单进程、单线程、嵌入式）
- 模块行数预算（硬约束）：
  - `type_system.py` ≤ 150 行
  - `pager.py` ≤ 250 行
  - `slotted_page.py` ≤ 220 行
  - `catalog.py` ≤ 100 行
  - `tokenizer.py` ≤ 200 行
  - `parser.py` ≤ 600 行
  - `executor.py` ≤ 400 行
  - `database.py` ≤ 100 行
  - 违反预算 = 违反 MVP 教学定位

## Out of Scope（本 change 明确不做）

- ACID 事务 / WAL / 崩溃恢复 → 留 `tinydb-acid`
- UPDATE 语句、WHERE 的 AND/OR/IN/LIKE → 留 `tinydb-engine-v2`
- ORDER BY、LIMIT、OFFSET、聚合、GROUP BY → 留 `tinydb-engine-v2`
- B-tree / 哈希索引 → 留 `tinydb-engine-v2`
- 列约束（PRIMARY KEY、NOT NULL、UNIQUE）→ 留 `tinydb-engine-v2`
- 扩展类型（VARCHAR、CHAR、DECIMAL、DATE、TIME、TIMESTAMP、SMALLINT、BIGINT）→ 留 `tinydb-engine-v2`
- CLI / REPL / psql 风格界面 → 留 `tinydb-engine-v2`
- 并发安全（多线程 / 多进程 / 文件锁）→ 不在任何 change 范围内（永久 out）
- ALTER TABLE、视图、触发器、外键、JOIN、用户/权限、网络协议 → 不在任何 change 范围内（永久 out）
