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
        if ref is None:
            return []
        rows = [read_slot(ref)]
    else:
        if is_single_eq_on_non_indexed_col(stmt.where, table):
            warn_stderr("table scan; consider adding index")
        rows = [r for r in scan_table(table) if eval_expr(stmt.where, r, ...)]
    return project(rows, stmt.columns)
```

## Decisions

### D1: B+tree 自实现

- 选项 A：自实现 ~350 行 ← 选 A
- 选项 B：引入 `btree` 库
- 理由：教学项目零运行时依赖；纯 Python B+tree ~350 行可控范围；标准库没有

### D2: schema_version 0x01 → 0x02 + file header 16B 不变

- 选项 A：magic byte 末位升级 ← 选 A
- 选项 B：扩展 file header 字段长度
- 理由：A 路线不动既有 16B 文件头结构；旧 .db 检测到 magic=`TINYDB\x00\x01` 走 v1 兼容加载（free list 视为空）

### D3: DROP 回收使用一次性扫描模式

- 选项 A：catalog 记录表关联的所有 page id（root + overflow + index root + index overflow），DROP 时 free ← 选 A
- 选项 B：扫描 free list 反查引用
- 理由：A 路线确定性 O(关联页数)；B 路线依赖反向引用，复杂度不值

### D4: DROP 回收 → 单事务语义

- 选项 A：best-effort（无事务保护；崩溃半回收由 `tinydb-acid` 处理） ← 选 A
- 选项 B：等待 `tinydb-acid` 完成后再做
- 理由：本 change 不强依赖 ACID；退化在 error message 可见

### D5: 复合索引本期不做

- 多列 B-tree 推迟；单列 UNIQUE 是本期重点

## Risks

- **R1**：B+tree split/merge bug 难复现 → 引入 fuzz 测（hypothesis）
- **R2**：页格式升级后旧 .db 反序列化失败 → fixture 准备 v1 + v2 .db 双格式
- **R3**：DROP 回收后 SELECT 路径访问到已被回收的 page id → 路径上严格 bounds check
- **R4**：B+tree 与 executor 之间的 key 编码不一致 → `type_system.encode_db_key(value, schema_type)` 统一入口
- **R5**：与 engine-v1 / constraints / aggregation 路径冲突 → 集成测覆盖整链

## Test Plan

- 单元 `tests/unit/test_btree.py`：insert/split/search/range/delete 边界
- 单元 `tests/unit/test_free_list.py`：alloc/free 平衡；head 维护正确
- 集成 `tests/integration/test_multi_page_catalog.py`：表数 > 阈值时溢出链稳定
- 集成 `tests/integration/test_index_manager.py`：INSERT 同步 B-tree；DELETE 同步；duplicate 抛 ConstraintViolation
- 集成 `tests/integration/test_select_uses_index.py`：SELECT WHERE PK = x 走 index；SELECT WHERE 非索引列仍全表扫描
- 反向：MVP + engine-v1 + constraints + aggregation 全套测试
- benchmark：n=10000 行全表扫 vs PK 索引扫对比，O(log n) 优势明显
