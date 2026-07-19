# Proposal: tinydb-engine-v2

> **范围声明**：本 change 重构存储层，引入**多页 catalog 溢出链 + 页回收 free list + B-tree 主键索引**。**SQL 语法与 parser 完全不动**（仍是已实现的 8 类 DDL/DML + 约束 + 聚合）；仅重写 `pager.py` / `catalog.py` / 新增 `btree.py`。**前置依赖**：`tinydb-constraints`（PRIMARY KEY / UNIQUE 已识别，索引接管执行路径）。

## Why

MVP + 三个后续 change 都基于单页 catalog（Page 1 一页 JSON），DROP 不回收页，PRIMARY/UNIQUE 校验为 O(n) 扫描。三大真实瓶颈：
1. **表数量 100+**：catalog 单页溢出
2. **频繁 CREATE+DROP TABLE**：磁盘只增不减
3. **WHERE col = x 走全表扫描**：`tinydb-engine-v1` 已暴露此限制；UNIQUE/PRIMARY KEY 走索引才不浪费

index 与 free list 是数据库基础能力的两大代表；这两个能力一次合发，可避免多次落地的中间态（P1: 多页 catalog 上线了但 page 仍泄漏；P2: B-tree 上线了但 catalog 单页还能塞下）。

## What Changes

- **修改** `Pager` 持久化格式增量：page 0 文件头追加 `FREE_LIST_HEAD: u32`（页 id，0 表示空）
- **新增** `Pager.alloc_page()` 优先从 free list 取，没有再 append，新页写入时登记 free 页（best-effort）
- **新增** `Pager.free_page(page_id)`：把页 id 头插到 free list（成功落盘才生效）
- **修改** `Catalog` 页格式：单页 JSON 改为"溢出链头 + 链长"。第一页仍是 `root_page_id=1`；catalog 表数 > 阈值时新增 overflow page，串联链表
- **新增** `btree.py`：实现简化版 B+tree（内部节点 fanout=16，叶节点键值对）
  - 键：列值的 `bytes()` 表示（统一编码接口）
  - 值：`u64` 行 slot 页 id + slot id
  - 操作：`insert(key, slot_ref)`、`search(key) -> slot_ref | None`、`range(start, end) -> Iterable[slot_ref]`、`delete(key)`
- **新增** `IndexManager`：表启动时为 PRIMARY KEY / UNIQUE 列创建 B-tree；INSERT / DELETE 同步维护；SELECT WHERE col = x 走 index
- **修改** SELECT WHERE executor 路径：优先 index lookup，回退全表扫描（带 stderr warning，"index miss" 不抛）
- **修改** DROP TABLE：catalog 移除后回收 root page 与 overflow 链 + 关联 B-tree 页

## Capabilities

### New Capabilities

- `storage-free-list`：Pager 维护 free list（head 页 id + 链表）；alloc 优先，free 头插
- `storage-multi-page-catalog`：Catalog 溢出链，JSON 段切分到多个 4KB 页
- `index-btree-primary`：B+tree on PRIMARY KEY 列；INSERT/DELETE/UPDATE 同步；SELECT WHERE PK = x 走 index
- `index-btree-unique`：B+tree on 任意 UNIQUE 列（每个 UNIQUE 列一棵 B-tree）

### Modified Capabilities

- `storage-engine`：`Pager.alloc_page` / `Pager.free_page` 重写；向后兼容旧 `.db` 文件（free_list_head=0 表示空）
- `schema-column-constraints`（来自 `tinydb-constraints`）：UNIQUE / PRIMARY KEY 校验从 O(n) 扫描改为 O(log n) B-tree 校验
- `sql-update-statement`（来自 `tinydb-engine-v1`）：UPDATE WHERE 路径走 index
- `storage-engine` / DROP：回收所有关联页

## Impact

- 受影响/新增文件：
  - `src/tinydb/pager.py`（重写 +~120 行）
  - `src/tinydb/catalog.py`（溢出链 +~80 行）
  - `src/tinydb/btree.py`（新增，~350 行）
  - `src/tinydb/index_manager.py`（新增，~150 行）
  - `src/tinydb/executor.py`（+~100 行 index lookup 分支）
- 模块行数：
  - `pager.py` ≤ 400 行
  - `catalog.py` ≤ 200 行
  - 新增 `btree.py` ≤ 400 行
  - 新增 `index_manager.py` ≤ 200 行
  - `executor.py` ≤ 920 行
- 测试：btree 单元 ~50、pager 集成 ~20、catalog 溢出链 ~15、index_manager ~15、回归反向全套
- 不引入外部依赖（B-tree 自实现）
- 不破坏外部 API（`Database.execute` 签名不变；catalog 反序列化向后兼容）

## Out of Scope（本 change 明确不做）

- ACID / WAL / 并发（→ `tinydb-acid`）
- 复合索引（multi-column B-tree）→ 后续可选增量
- 倒排索引 / 全文索引 → 永久 out
- Hash index → 永久 out
- 索引条件推送（index-only scans）→ 后续
- 索引统计信息 / ANALYZE → 永久 out
