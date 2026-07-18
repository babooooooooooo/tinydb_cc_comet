---
comet_change: tinydb-engine-v2
role: technical-design
canonical_spec: openspec
status: final
---

# Design: tinydb-engine-v2

> **关联文档**：[proposal.md](../../../../openspec/changes/tinydb-engine-v2/proposal.md) · [design.md](../../../../openspec/changes/tinydb-engine-v2/design.md) · [tasks.md](../../../../openspec/changes/tinydb-engine-v2/tasks.md)
> **Brainstorm checkpoint**：[brainstorm-summary.md](../../../../openspec/changes/tinydb-engine-v2/.comet/handoff/design-context.md)
> **Date**：2026-07-19
> **承接 change 名**：`tinydb-engine-v2`

本文档落实六轮澄清与五段设计裁决，提供实现级技术方案供 build 阶段 implementer 直接对照。

---

## 1. Context

MVP + 四个后续 change（engine-v1、constraints、types、aggregation）共享三个存储层瓶颈：

1. **Catalog 单页（4KB）** —— 表元数据以 JSON 存放在 page 1；超过 50 张表即溢出页面。
2. **无页回收** —— `DROP TABLE` 只移除 catalog 条目，表的数据页泄漏；文件大小只增不减。
3. **O(n) 唯一/PK 校验** —— `_validate_unique_keys` 在每次 INSERT 时遍历全部行。SELECT WHERE 命中索引列时也全表扫描。

三者紧密耦合：free list 同时服务 DROP 回收与 B+tree 节点分配；多页 catalog 与 B+tree 持久化共享同一页面级 API。

## 2. Goals / Non-Goals

**Goals：**
- Pager header 升级为 `schema_version=0x02` + `free_list_head: u32`。旧 v1 文件开打时自动升级。
- `Pager.alloc_page()` 优先查询 free list 表头；`Pager.free_page()` 头插释放页。
- Catalog 在 JSON 超出一页时通过溢出链跨多页。
- B+tree 自实现（每节点一 4KB 页，fanout=16）。删除仅置 tombstone（不主动 merge）。
- `IndexManager` 索引 PRIMARY KEY 与 UNIQUE 列。INSERT/UPDATE/DELETE 维护 B+tree。SELECT WHERE 命中索引列走索引查找；miss 返回空。
- 约束校验（UNIQUE / PK）改用 B+tree 查找，替代 O(n) 扫描。
- DROP TABLE 通过 `Pager.free_page()` 回收 data + index + overflow chain 页。
- 向后兼容：既有 `.db` 文件开打时自动升级到 v2；索引在首次访问该表时惰性构建。

**Non-Goals：**
- ACID / WAL / 事务（→ `tinydb-acid`）
- 并发 / 多线程
- 复合 / 多列索引
- 倒排索引 / 全文索引 / Hash 索引
- ANALYZE / 统计信息
- B+tree merge / underflow 再平衡（仅 tombstone）
- Index-only scans（仅从索引读全部列）
- tombstone 密集页的周期性 compaction / GC

## 3. Architecture

### 3.1 Pager v2 header + free list

**文件头（page 0）：**
```
bytes  0-7:   magic          b'TINYDB\x00\x02'   # 版本 0x02
byte   8:     schema_version 0x02
bytes  9-12:  free_list_head u32                  # free 页链头，0 = 空
bytes 13-4095: reserved (zeros)
```

**Free list 语义：**
- 释放页的前 4 字节（offset 0）解释为 `u32 next_free_page_id`（0 = 链尾）。
- 释放状态下，page 第 4 字节之后的内容不使用。
- `Pager.alloc_page()`：若 `free_list_head != 0`，读取该页 next_free、更新 header；返回该页 id。否则追加新页。
- `Pager.free_page(page_id)`：在 page[0:4] 写入 `next_free = free_list_head`；更新 header 的 `page_id`。其余内容 best-effort。

**向后兼容（v1 → v2）：**
- `Pager.open()` 时若 `schema_version=0x01`：把 byte 8 改写为 0x02，在 bytes 9-12 写入 `free_list_head=0`。不迁移数据。
- IndexManager 在每张表的首次 INSERT/SELECT 时惰性构建（一次全表扫描 → B+tree）。

**模块行数预算：** `pager.py ≤ 400`（原 169；+~120 行）。

### 3.2 Catalog overflow chain

**磁盘格式：**
- Page 1 = chain head。每条 chain page 容纳一段 JSON segment：`{"tables": {...}, "_seg_index": N, "_seg_count": M}`。
- 非末页的 page 起始 4 字节为 `u32 next_page`（0 = 末页），其后是 JSON segment。
- 溢出触发：增加下一张表条目会让 page payload 超过 `PAGE_SIZE - 16` 字节（16 = chain 元数据 + 安全冗余）。

**写路径（`Catalog.to_bytes` → chain）：**
1. 序列化 JSON。若能放进 `PAGE_SIZE - 16` 字节，直接写入 page 1（无 chain）。
2. 否则贪婪切分到多页，`Pager.alloc_page()` 分配新 chain page，逐段写入。

**读路径（`Catalog.from_bytes`）：**
1. 沿 chain head → tail 走完整链。
2. 拼接 JSON segments（剥离 `_seg_index` / `_seg_count`，或保留作调试信息）。
3. `json.loads` 一次。

**模块行数预算：** `catalog.py ≤ 250`（原 169；+~80 行）。

### 3.3 B+tree

**Page 布局（4KB = 4096 字节）：**
```
byte 0:      node_type     u8     (1 = leaf, 2 = internal)
byte 1:      reserved      u8     (0)
bytes 2-3:   key_count     u16
bytes 4-5:   reserved      u16    (0)
bytes 6-9:   next_leaf_id  u32    (仅 leaf；0 = 无下个 leaf；internal = 0)
bytes 10-4095: payload     4086 bytes
```

**内部节点 payload：**
- `keys[0..key_count-1]`（每条 = key_size 字节；定长类型定长，变长类型变长）
- `children[0..key_count]`（每条 = u32 page_id；key_count+1 个子节点）

**叶子节点 payload：**
- `keys[0..key_count-1]`（每条 = key_size 字节，与内部一致）
- `values[0..key_count-1]`（每条 = u32 row_page_id << 32 | slot_id，u64 打包）
- Tombstone：每条 entry 1 bit，packed 在 entry metadata byte 中（详见 § 3.3.3）

**3.3.1 Key 编码**（Q1 答复 —— `codec_for()`）：

| 类型 | 编码 | 宽度 |
|------|------|------|
| `SMALLINT` | big-endian signed i16 | 2 字节（定长） |
| `INT` / `INTEGER` | big-endian signed i32 | 4 字节（定长） |
| `BIGINT` | big-endian signed i64 | 8 字节（定长） |
| `FLOAT` / `REAL` | big-endian IEEE 754 单精度（u32 位） | 4 字节（定长） |
| `DOUBLE` | big-endian IEEE 754 双精度（u64 位） | 8 字节（定长） |
| `DATE` | big-endian signed i32（自 UTC 1970-01-01 起天数） | 4 字节（定长） |
| `TIME` | big-endian unsigned u32（自 UTC 当日起秒数） | 4 字节（定长） |
| `TIMESTAMP` | big-endian signed i64（自 UTC 1970-01-01 起秒数） | 8 字节（定长） |
| `VARCHAR(N)` | u16 长度前缀 + UTF-8 字节 | 2 + utf8_len（变长） |
| `CHAR(N)` | 定长 N 字节（右空格填充） | N 字节（定长） |
| `TEXT` | u32 长度前缀 + UTF-8 字节 | 4 + utf8_len（变长） |
| `DECIMAL(p,s)` | big-endian signed i64（scaled） | 8 字节（定长） |
| `BOOL` / `BOOLEAN` | u8（0 / 1） | 1 字节（定长） |

NULL 不入索引（沿用 constraints change 的 R9 SQL 标准语义）。变长键（VARCHAR/TEXT）走单独的 leaf payload 格式：每条 entry 存 `(u16 key_len, key_bytes, u64 value)`，而非 `(fixed_key, u64 value)`。

**3.3.2 操作：**
- `insert(key, slot_ref)`：下降至 leaf 插入；leaf 满则按中位 key 拆分并提升至父节点；向上递归；根节点分裂分配新 root 页（树高增长）。
- `search(key) -> SlotRef | None`：按 key 比较下降。
- `range(start, end) -> Iterable[SlotRef]`：下降至起始 leaf，沿 `next_leaf_id` 迭代直至 key > end。
- `delete(key)`：下降至 leaf，将 entry 标记为 tombstone（in-band flag 位；不主动 merge —— 详见 § 3.3.3）。

**3.3.3 Tombstone 语义**（Q3 答复 —— 不主动 merge）：
- 每个 leaf entry 的 tombstone flag 存在 entry metadata byte（key 之前的 1 字节）。标记 tombstone 后：entry 保留原位，`search` 跳过。
- 周期性 compaction（重建 tombstone 密集页）明确 out of scope。

**模块行数预算：** `btree.py ≤ 400`（新文件，约 350 行）。

### 3.4 IndexManager + executor 路由

**`IndexManager`** 持有 `dict[(table_name, column_name), BTreeRootPageId]`。

**`Database.open()` 时：**
- 加载 catalog（走 overflow chain）。
- 对每张表调用 `rebuild_for_table(table)`：全表扫描，对每个索引列（PK 或 UNIQUE）执行 `BTree.insert(encoded_key, SlotRef(page_id, slot_id))`。

**`INSERT` 时：**
- 写入 slot 成功后：对每个索引列调用 `index_manager.insert(table, col, encoded_key, slot_ref)`。
- 写前检查：对每个索引列调用 `index_manager.lookup(...)`；若命中 → 抛 `ConstraintViolation`（替代 O(n) `_scan_unique_keys`）。
- 失败回滚：删除 slot，对已完成部分插入调用 `index_manager.delete(...)`。

**`DELETE` 时：**
- 通过 `index_manager.lookup(...)` 并结合 WHERE 过滤定位 slot。
- 删除 slot，对每个索引列调用 `index_manager.delete(...)`。

**`UPDATE` 时：**
- 对值变化的索引列：先 `delete(old_key)` 后 `insert(new_key)`。

**`SELECT WHERE col = lit` 时**（Q4 答复 —— 始终用索引，不回退扫描）：
- 若 `(table, col)` 在 `index_manager` 中且 WHERE 为单等值（无 AND/OR）：调用 `index_manager.lookup(...)` 拿 slot_ref，读这一条 slot。
- 索引 miss → 返回空结果（无 stderr 警告）。

**模块行数预算：** `index_manager.py ≤ 200`（新文件，约 150 行）；`executor.py ≤ 920`（原 707；+~100 行索引查找路径）。

### 3.5 DROP TABLE 回收

**`DROP TABLE` 时：**
1. 走数据页（读 root，沿 chain 下降）→ 收集 data page id。
2. 对每个索引列：走其 B+tree（读 root，沿 child 指针下降）→ 收集 index page id。
3. `Pager.free_page()` 逐个释放收集到的页 id。
4. 从 catalog 移除该表。
5. Catalog 重新序列化至 overflow chain（若占用整页可能缩短）。

**模块行数预算：** 含在 `executor.py` 内（上文已计）。

## 4. Spec decisions（D1–D6）

### D1：索引键编码
按 § 3.3.1 —— `codec_for(type, type_params).encode_py(value)` 产出定长可排序字节。NULL 不入索引。

### D2：Fanout
Fanout = 每 leaf/internal 节点 16 key。最大 key 8 字节（BIGINT / DECIMAL / DOUBLE / TIMESTAMP）+ 4 字节 child pointer，internal 节点在 4KB 内可容纳 ~16 key。短键类型（INT = 4 字节）每页装入更多 key。

### D3：删除仅 tombstone（不主动 merge）
按 Q3 答复 —— entry 通过 in-band flag 标记删除。树形不变。compaction 延后。

### D4：始终用索引（不回退扫描）（Q4 答复）
SELECT WHERE 命中索引列永不下放扫描。非索引列继续全表扫描。

### D5：DROP 全回收（Q6 答复）
data + index + overflow chain 页全部归还 free list。

### D6：v1 → v2 自动升级（Q5 答复）
`Pager.open()` 改写 header byte 8 为 0x02 并写入 `free_list_head=0`。索引在每张表的首次 INSERT/SELECT 时惰性构建。

## 5. Capabilities

### New Capabilities

- `storage-free-list`：Pager 维护 free list（head page id + 链）；alloc 查询，free 头插。
- `storage-multi-page-catalog`：Catalog JSON 超出一页时跨溢出链。
- `index-btree-primary`：PRIMARY KEY 列上的 B+tree；INSERT/DELETE/UPDATE 维护；SELECT WHERE PK = lit 走索引。
- `index-btree-unique`：任意 UNIQUE 列上的 B+tree（每 UNIQUE 列一棵）。

### Modified Capabilities

- `storage-engine`（来自 MVP）：`Pager.alloc_page` / `Pager.free_page` 重写；v1 → v2 开打时自动升级。
- `schema-column-constraints`（来自 `tinydb-constraints`）：UNIQUE / PRIMARY KEY 校验由 O(n) 扫描改为 O(log n) B+tree 查找。
- `sql-update-statement`（来自 `tinydb-engine-v1`）：UPDATE WHERE 路径在适用场景走索引。
- DROP TABLE 行为：回收 data + index + overflow chain 页。

## 6. 文件 / 模块影响

| 文件 | 状态 | 行数预算 | 说明 |
|------|------|----------|------|
| `src/tinydb/pager.py` | 修改 | ≤ 400 | +free list、v2 header、v1 升级 |
| `src/tinydb/catalog.py` | 修改 | ≤ 250 | +overflow chain 序列化与遍历 |
| `src/tinydb/btree.py` | 新增 | ≤ 400 | B+tree 节点、insert、search、range、delete（tombstone） |
| `src/tinydb/index_manager.py` | 新增 | ≤ 200 | (table, col) → BTree root 映射；rebuild_for_table |
| `src/tinydb/executor.py` | 修改 | ≤ 920 | +INSERT/UPDATE/DELETE/SELECT 索引查找路径；+DROP 回收 |
| `src/tinydb/database.py` | 修改 | （无新预算） | open 时初始化 IndexManager |
| `tests/unit/test_btree.py` | 新增 | — | ~20 测试：insert/split/search/range/delete/tombstone |
| `tests/unit/test_free_list.py` | 新增 | — | ~10 测试：alloc/free 循环、链遍历 |
| `tests/unit/test_index_manager.py` | 新增 | — | ~10 测试：rebuild、lookup、insert、delete |
| `tests/integration/test_pager_v2_header.py` | 新增 | — | ~5 测试：v1 升级、magic 校验 |
| `tests/integration/test_catalog_overflow.py` | 新增 | — | ~10 测试：chain、遍历、重开后持久化 |
| `tests/integration/test_select_uses_index.py` | 新增 | — | ~10 测试：PK/UNIQUE 路由、非索引列扫描 |
| `tests/integration/test_drop_reclaims_pages.py` | 新增 | — | ~5 测试：data + index + chain 回收 |
| `tests/perf/test_index_vs_scan.py` | 新增 | — | ~3 基准：n=10000 时 PK 查找 vs 全表扫描 |
| 既有测试 | 不变 | — | MVP / engine-v1 / constraints / types / aggregation 全部继续通过 |

**外部 API：** `Database.execute()` 签名不变。`Pager.read_page()` / `write_page()` / `alloc_page()` / `free_page()` 签名稳定（仅 alloc/free 语义变化）。

## 7. Out of Scope

- ACID / WAL / 事务 → `tinydb-acid`
- 复合索引（多列 B+tree）→ 后续
- 倒排 / 全文 / Hash 索引 → 永久
- ANALYZE / 统计信息 → 永久
- B+tree merge / underflow 再平衡 → 后续 compaction change
- Index-only scans → 后续
- tombstone 密集页的周期性 compaction → 后续
- B+tree 并发访问 → 永久（单线程 scope）

## 8. 测试策略

**单元测试：**
- `test_btree.py`：insert/split/search/range/delete 在小树上（5-50 key）；root 分裂；多页 leaf；tombstone 标记。
- `test_free_list.py`：alloc/free 循环；链遍历；free list 跨重启持久化。
- `test_index_manager.py`：rebuild_for_table；lookup miss/hit；insert/delete 维护。

**集成测试：**
- `test_pager_v2_header.py`：开 v1 文件 → header 升级；开 v2 文件 → 不变；坏 magic 抛错。
- `test_catalog_overflow.py`：100 张表的 catalog 跨重启持久化；chain 遍历；DROP 后页回收。
- `test_select_uses_index.py`：SELECT WHERE PK 走索引；UNIQUE 走索引；非索引列下放扫描。
- `test_drop_reclaims_pages.py`：DROP 归还 data + index 页至 free list；后续 INSERT 复用。

**性能基准**（在 `tests/perf/` 下）：
- `test_index_vs_scan.py`：n=10000 时 PK 查找 < 全表扫描 / 100（验收标准对应提案 §F6）。

**回归：**
- 既有完整测试套件（MVP / engine-v1 / constraints / types / aggregation）继续通过，无修改。
- 覆盖率整体 ≥ 90%；新代码 100%。

## 9. Risks

| Risk | 概率 | 缓解 |
|------|------|------|
| B+tree split bug（中位 off-by-one、child 指针丢失） | 中 | 单元测试在每次操作后校验不变量（key count、有序） |
| v1 → v2 升级让既有文件不一致（写入 free_list_head 但页实际未 free） | 低 | 升级只改 header 字节；不动数据页；无路径将"旧数据页"解释为 free |
| IndexManager.rebuild 在大表上慢 | 中 | 首次成本可接受（~1ms / 10k 行）；延迟优化：INSERT 时增量构建 |
| Tombstone 累积拖累 search | 低 | Out of scope：周期性 compaction。作为已知限制写进 `MVP_LIMITATIONS.md` |
| Pager 行数预算超标（原 169，可能超 400） | 低 | 需要时拆出 `_page_alloc.py` / `_free_list.py`；延后 |

## 10. 验收标准

- 既有测试全部通过（575+）。
- 新增测试：~50 单元 + ~30 集成 + ~3 性能。
- 覆盖率整体 ≥ 90%；`btree.py` / `index_manager.py` 100%。
- 模块行数预算严格遵守。
- v1 `.db` 文件开打无错；首次 INSERT/SELECT 触发 IndexManager rebuild。
- DROP 释放所有 data + index + overflow 页（通过 `page_count()` 减少验证）。
- n=10000 时 PK 查找 < 全表扫描 / 100。