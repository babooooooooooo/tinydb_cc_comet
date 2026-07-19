# Tasks: tinydb-engine-v2

> **前置**：`tinydb-constraints` 已合并。

## 1. Pager 文件头升级

- [x] 1.1 编写 `tests/integration/test_pager_v2_header.py::test_old_file_v1_loads_with_empty_free_list`，红
- [x] 1.2 在 `pager.py` 把 `MAGIC` 末位 byte 升到 `\x02`、`SCHEMA_VERSION = 0x02`；新增 `free_list_head: u32` 字段；旧文件反序列化时 free list 视为空
- [x] 1.3 编写 `test_pager_alloc_uses_free_list_before_extending_file`，绿

## 2. Free list

- [x] 2.1 编写 `tests/unit/test_free_list.py::test_free_then_alloc_recycles_same_page`，红
- [x] 2.2 实现 `Pager.alloc_page()`：先读 free_list_head，非 0 则取出首节点，文件头更新；否则 append
- [x] 2.3 实现 `Pager.free_page(page_id)`：将被释放页写入"next_free = head"；更新文件头

## 3. Catalog 多页溢出链

- [x] 3.1 编写 `tests/integration/test_catalog_overflow.py::test_overflow_chain_persists_across_reopen`，红
- [x] 3.2 在 `catalog.py` 把 TableInfo 列表 JSON 序列化分片到多个 4KB page；维护 overflow 链表（page 1 是 head，新页链在 head 后续）
- [x] 3.3 实现 `Catalog.from_bytes` 走链表完整恢复
- [x] 3.4 `Pager` 提供 `catalog_pages_for(table_ids)` API

## 4. B+tree 实现

- [x] 4.1 编写 `tests/unit/test_btree.py::test_btree_insert_split_search`，红
- [x] 4.2 在 `btree.py` 实现 B+tree 节点（InternalNode / LeafNode）+ 序列化到 4KB page
- [x] 4.3 实现 `BTree.insert` 含 split 操作
- [x] 4.4 编写 `test_btree_search_returns_none_when_missing`，绿
- [x] 4.5 实现 `BTree.search(key)`
- [x] 4.6 编写 `test_btree_range_iterates_in_order`，绿
- [x] 4.7 实现 `BTree.range(start, end)`
- [x] 4.8 编写 `test_btree_delete_marks_tombstone`，绿
- [x] 4.9 实现 `BTree.delete`（本期不强制 merge，tombstone 即可）

## 5. Index Manager

- [x] 5.1 编写 `tests/integration/test_index_manager.py::test_pk_btree_maintained_on_insert_delete`，红
- [x] 5.2 在 `index_manager.py` 实现 `IndexManager.rebuild_for_table`：遍历表所有行构建 B+tree
- [x] 5.3 接入 INSERT executor：每插一行同步插入 PK/UNIQUE 列 B+tree
- [x] 5.4 接入 DELETE executor：每删一行同步删除 B+tree entry
- [x] 5.5 接入 UPDATE（来自 engine-v1）：列值变化时 update B+tree entry

## 6. SELECT WHERE 走 index

- [x] 6.1 编写 `tests/integration/test_select_uses_index.py::test_select_pk_eq_uses_btree`，红
- [x] 6.2 在 SELECT executor 主路径上识别 `col = literal` 且 col 为 indexed 列则走 `IndexManager.lookup`
- [x] 6.3 编写 `test_select_non_indexed_eq_warns_to_stderr`，绿（保留 client 兼容行为）

## 7. DROP TABLE 回收

- [x] 7.1 编写 `tests/integration/test_drop_reclaims_pages.py::test_drop_frees_table_and_index_pages`，红
- [x] 7.2 在 `executor.py::execute_drop_table` 收集该表所有关联 page（root + overflow + index root + index overflow）
- [x] 7.3 一次性 `free_page` 全部；更新 catalog

## 8. 兼容与回归

- [x] 8.1 MVP 旧 `.db` 文件加载路径保持原行为（free list 视为空）
- [x] 8.2 `engine-v1` / `constraints` / `aggregation` 既有测试全绿
- [x] 8.3 模块行数：`pager.py ≤ 400`、`catalog.py ≤ 200`、`btree.py ≤ 400`、`index_manager.py ≤ 200`、`executor.py ≤ 920`
- [x] 8.4 覆盖率 ≥ 90%；新代码 100%
- [x] 8.5 性能基准：PK 索引扫 < 全表扫 / 100（n=10000）
