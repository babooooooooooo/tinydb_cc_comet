# Comet Design Handoff

- Change: tinydb-engine-v2
- Phase: design
- Mode: compact
- Context hash: d76f40557694e4fedfb04cb941160f0b7a82eb710efeaf3e91f7464ac81a4cdc

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tinydb-engine-v2/proposal.md

- Source: openspec/changes/tinydb-engine-v2/proposal.md
- Lines: 1-69
- SHA256: c27cc4e359216c33c9d67d533f25f486e91026ecddeef9b016d3ad81f56be417

```md
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

```

## openspec/changes/tinydb-engine-v2/design.md

- Source: openspec/changes/tinydb-engine-v2/design.md
- Lines: 1-137
- SHA256: c714f8d876d213d412ec9e931f363bdf3ab500c2ed69cadd43e27038e5b669cf

[TRUNCATED]

```md
# Design: tinydb-engine-v2

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

本 change 重构存储层。约束（constraints）已表达 PRIMARY KEY / UNIQUE 元信息，本 change 把这些元信息"机器化"——B-tree index 给定 col + slot_ref 的 O(log n) 查询路径。

## Goals / Non-Goals

**Goals：**
- 多页 catalog 溢出链稳定
- Free list head 字段在 page 0 文件头；alloc 优先；free 头插
- B+tree 自实现，足够正确（key 顺序、split、merge、并发安全不强求）
- PRIMARY KEY 在 INSERT 时维护 B-tree；duplicate 走原 UNIQUE 校验路径抛 `ConstraintViolation`
- SELECT WHERE PK col = x：检测到 B-tree 存在则走 index，否则全表扫描 + stderr warning
- DROP TABLE 回收 root_page + overflow + 所有 index 页
- 旧 `.db` 文件（无 free_list_head）能加载：free_list_head=0 视为空

**Non-Goals（本期不做）：**
- 不引入 WAL / 事务
- 不引入并发
- 不引入复合索引
- 不引入 ANALYZE / 统计
- 不引入 B-tree 并发安全（单线程场景）

## Architecture

### 文件头升级

```
struct FileHeader {
    magic:       bytes 8  ("TINYDB\x00\x02")   # 版本从 0x01 升到 0x02
    schema_ver:  u8     (0x02)
    page_size:   u32    (4096)
    free_list:   u32    (page_id | 0)
}
```

文件头大小不变（仍 16 字节；magic 1 byte 用 \x02 标识 schema 升级，page_size 后 4 字节挪给 free_list）。

### B+tree 设计

简化 B+tree（与经典 B+tree 同构但省略范围优化）：

- 内部节点：keys[i] + children[i+1]；key 数 ∈ [fanout/2, fanout]
- 叶节点：keys[i] + values[i]；内部 next_leaf 指针（双向可选）
- fanout = 16（4KB 页 / 256 字节 entry ≈ 16）
- split 触发：插入时 keys 满 → 一分为二，提升中间 key
- merge：删除时 keys < fanout/2 → 与 sibling 合并；本期可简化为不主动 merge（保留 tombstone，依赖次轮 garbage collection）；MVP/教学场景删除少

### IndexManager

```python
class IndexManager:
    def __init__(self, pager, catalog):
        self.pager = pager
        self.catalog = catalog
        self.indexes: dict[(table_name, column_name), BTree] = {}

    def rebuild_for_table(self, table):
        for col in table.columns:
            if col.primary_key or col.unique:
                bt = BTree(self.pager)
                for row in scan_table(table):
                    bt.insert(encode_key(row[col.name]), SlotRef(row.page_id, row.slot_id))
                self.indexes[(table.name, col.name)] = bt

    def lookup(self, table_name, column_name, key) -> SlotRef | None: ...
    def insert(self, table_name, column_name, key, ref) -> None: ...
    def delete(self, table_name, column_name, key) -> None: ...
```

### SELECT WHERE 路径分流

```python
def execute_select(stmt, table, ...):
    if stmt.where and is_single_eq_on_indexed_column(stmt.where, table, index_manager):
        col, value = parse_eq(stmt.where)
        ref = index_manager.lookup(table.name, col, value)

```

Full source: openspec/changes/tinydb-engine-v2/design.md

## openspec/changes/tinydb-engine-v2/tasks.md

- Source: openspec/changes/tinydb-engine-v2/tasks.md
- Lines: 1-62
- SHA256: 54fb49645a9523199f8f49f08109ab54bc60d4b9e39f84af15c2ad6a9df663e7

```md
# Tasks: tinydb-engine-v2

> **前置**：`tinydb-constraints` 已合并。

## 1. Pager 文件头升级

- [ ] 1.1 编写 `tests/integration/test_pager_v2_header.py::test_old_file_v1_loads_with_empty_free_list`，红
- [ ] 1.2 在 `pager.py` 把 `MAGIC` 末位 byte 升到 `\x02`、`SCHEMA_VERSION = 0x02`；新增 `free_list_head: u32` 字段；旧文件反序列化时 free list 视为空
- [ ] 1.3 编写 `test_pager_alloc_uses_free_list_before_extending_file`，绿

## 2. Free list

- [ ] 2.1 编写 `tests/unit/test_free_list.py::test_free_then_alloc_recycles_same_page`，红
- [ ] 2.2 实现 `Pager.alloc_page()`：先读 free_list_head，非 0 则取出首节点，文件头更新；否则 append
- [ ] 2.3 实现 `Pager.free_page(page_id)`：将被释放页写入"next_free = head"；更新文件头

## 3. Catalog 多页溢出链

- [ ] 3.1 编写 `tests/integration/test_catalog_overflow.py::test_overflow_chain_persists_across_reopen`，红
- [ ] 3.2 在 `catalog.py` 把 TableInfo 列表 JSON 序列化分片到多个 4KB page；维护 overflow 链表（page 1 是 head，新页链在 head 后续）
- [ ] 3.3 实现 `Catalog.from_bytes` 走链表完整恢复
- [ ] 3.4 `Pager` 提供 `catalog_pages_for(table_ids)` API

## 4. B+tree 实现

- [ ] 4.1 编写 `tests/unit/test_btree.py::test_btree_insert_split_search`，红
- [ ] 4.2 在 `btree.py` 实现 B+tree 节点（InternalNode / LeafNode）+ 序列化到 4KB page
- [ ] 4.3 实现 `BTree.insert` 含 split 操作
- [ ] 4.4 编写 `test_btree_search_returns_none_when_missing`，绿
- [ ] 4.5 实现 `BTree.search(key)`
- [ ] 4.6 编写 `test_btree_range_iterates_in_order`，绿
- [ ] 4.7 实现 `BTree.range(start, end)`
- [ ] 4.8 编写 `test_btree_delete_marks_tombstone`，绿
- [ ] 4.9 实现 `BTree.delete`（本期不强制 merge，tombstone 即可）

## 5. Index Manager

- [ ] 5.1 编写 `tests/integration/test_index_manager.py::test_pk_btree_maintained_on_insert_delete`，红
- [ ] 5.2 在 `index_manager.py` 实现 `IndexManager.rebuild_for_table`：遍历表所有行构建 B+tree
- [ ] 5.3 接入 INSERT executor：每插一行同步插入 PK/UNIQUE 列 B+tree
- [ ] 5.4 接入 DELETE executor：每删一行同步删除 B+tree entry
- [ ] 5.5 接入 UPDATE（来自 engine-v1）：列值变化时 update B+tree entry

## 6. SELECT WHERE 走 index

- [ ] 6.1 编写 `tests/integration/test_select_uses_index.py::test_select_pk_eq_uses_btree`，红
- [ ] 6.2 在 SELECT executor 主路径上识别 `col = literal` 且 col 为 indexed 列则走 `IndexManager.lookup`
- [ ] 6.3 编写 `test_select_non_indexed_eq_warns_to_stderr`，绿（保留 client 兼容行为）

## 7. DROP TABLE 回收

- [ ] 7.1 编写 `tests/integration/test_drop_reclaims_pages.py::test_drop_frees_table_and_index_pages`，红
- [ ] 7.2 在 `executor.py::execute_drop_table` 收集该表所有关联 page（root + overflow + index root + index overflow）
- [ ] 7.3 一次性 `free_page` 全部；更新 catalog

## 8. 兼容与回归

- [ ] 8.1 MVP 旧 `.db` 文件加载路径保持原行为（free list 视为空）
- [ ] 8.2 `engine-v1` / `constraints` / `aggregation` 既有测试全绿
- [ ] 8.3 模块行数：`pager.py ≤ 400`、`catalog.py ≤ 200`、`btree.py ≤ 400`、`index_manager.py ≤ 200`、`executor.py ≤ 920`
- [ ] 8.4 覆盖率 ≥ 90%；新代码 100%
- [ ] 8.5 性能基准：PK 索引扫 < 全表扫 / 100（n=10000）

```
