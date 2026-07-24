# Brainstorm Summary

- Change: join-query
- Date: 2026-07-23

## 确认的技术方案

- 基于当前 main 的 v0.1 parser、executor、Catalog、Pager、WAL、B+Tree 和 Database API 增量扩展，不修改磁盘格式，不重写存储引擎。
- 支持 `INNER`、`LEFT`、`RIGHT`、`FULL`、`CROSS JOIN`，以及 `ON`、`USING (...)`、`NATURAL`；支持多表、表别名、限定列和复杂布尔谓词。
- 保留 AST 作为语法层，在 AST 与执行器之间增加无副作用 LogicalPlan；后续 CLI `.explain` 消费该计划。
- JOIN 结果继续进入 WHERE、投影、ORDER BY、LIMIT/OFFSET、GROUP BY、HAVING 和聚合；单表无 JOIN 继续保留 v0.1 兼容路径。
- 执行层采用 LogicalPlan + 物化宽行：先构造连接结果列表，再通过统一 resolver 复用过滤、聚合、排序和投影阶段。

## 关键取舍与风险

- 已确认不采用懒迭代器作为 v0.2 最低实现，以避免重构现有聚合和排序管线；物化宽行的大结果集内存占用是明确 trade-off。
- 当前代码的 `Select`、`EqualsExpr`、聚合 helper 和 `Database.execute` 仍以单表裸列名为中心，限定列、合并 schema、USING/NATURAL 输出和 Row 映射需要统一 resolver。
- `executor.py` 已较大；JOIN/resolver/plan/连接 helper 应拆为小模块，避免继续堆积。
- RIGHT/FULL/USING/NATURAL 的 NULL 语义、输出顺序和合并列标签需要在设计阶段锁定。
- 已确认外连接顺序采用 `strict-left-deep-insertion`：匹配行按用户书写左深 nested-loop 的输入顺序，LEFT 未匹配行紧跟其左行，RIGHT/FULL 未匹配行在所有匹配行之后按该侧表扫描顺序追加；无 ORDER BY 时此顺序是可测试的稳定契约。
- 已确认 NATURAL JOIN 无共同列时退化为 CROSS JOIN（笛卡尔积），与 CROSS JOIN 共享执行路径，错误信息提示"按 CROSS JOIN 处理"。
- 已确认 USING/NATURAL 合并列取值采用 Coalesce：先取左侧值，若左侧为 NULL 则取右侧；输出标签为未限定列名（如 `id`），USING/NATURAL 合并键只出现一次；RIGHT/FULL 的 NULL 补齐场景使用 None 标记缺失侧。
- 已确认引入 `tinydb.ResolutionError`，作为 `ExecutionError` 子类；覆盖未知表、未知限定列、歧义裸列、USING 缺失列、合并键类型不兼容等名称解析错误；delta spec 明确区分 parser 阶段与 resolver 阶段错误。
- 已确认采用方案 B（resolver + plan + JOIN helper）：`resolver.py`、`plan.py`、`_join_executor.py` 三个独立模块，`executor.py` 仅负责 dispatch；单表无 JOIN 保留 v0.1 fast path；`.explain` 直接消费 LogicalPlan。
- 已确认 v0.2 不加硬性 JOIN 行数上限；`MVP_LIMITATIONS.md` 明确声明大结果集可能导致 OOM，跟 v0.1 风格一致；`max_join_rows` 作为 follow-up 备选。

## 已确认的完整技术设计（A–H 节）

### A. 数据流与架构

SQL → tokenizer → parser (Select AST) → resolver (表映射+合并 schema+USING/NATURAL key) → build_plan (LogicalPlan) → executor (dispatch：含 JOIN → _join_executor.execute_plan；单表 → v0.1 fast path) → Row 包装。

### B. JOIN 执行与外连接契约

左深 nested-loop；LEFT 未匹配右补 NULL 紧跟左行；RIGHT 内部交换左右复用 LEFT；FULL 未匹配行在匹配行后按该侧扫描顺序追加；输出顺序 = strict-left-deep-insertion。

### C. 名称解析与合并 schema

来源映射 + 限定列 + 唯一裸列 + 歧义报错；USING/NATURAL 合并键 Coalesce 取值；多表 SELECT * 使用 source.column 限定标签；NATURAL 无共同列退化为 CROSS JOIN。

### D. LogicalPlan 节点

Scan / Join / Filter / Aggregate / Sort / Project / Limit 全部 frozen dataclass；`build_plan(ast, catalog)` 集中构造；暴露只读 `Database.explain_plan(sql)`。

### E. 错误类型

parser 阶段用 ParseError；resolver/build_plan 阶段用 `ResolutionError`（ExecutionError 子类）；执行期用 ExecutionError；统一从 tinydb 命名空间再导出。

### F. Row 输出与 API

JOIN Row 用 source.column 限定标签；USING/NATURAL 合并键单独未限定标签；映射访问 `row["u.id"]` / `row["id"]`；单表 Row 行为不变。

### G. 测试策略

tokenizer/parser/resolver/plan/join 单元；JOIN × ON/USING/NATURAL × 多级 × NULL 集成；JOIN 后阶段 × 聚合 × 排序；错误诊断；property 验证 strict-left-deep-insertion；ACID 回归；覆盖率 ≥ v0.1；pyflakes + OpenSpec strict validation。

### H. Spec Patch 候选

- sql-join-query/spec.md：NATURAL 无共同列退化为 CROSS、USING/NATURAL Coalesce 取值、strict-left-deep-insertion 顺序契约；
- python-api/spec.md：ResolutionError 再导出、JOIN Row 映射访问；
- sql-minimal-parser/spec.md：NATURAL 自动发现同名列。

## 测试策略

- 设计阶段拟覆盖 tokenizer/parser、名称解析、plan 构造无副作用、各 JOIN 类型、ON/USING/NATURAL、NULL、JOIN 后聚合/排序/API、单表回归和 E2E/property。
- 需要增加物化结果的内存/大结果边界测试，确保行为确定且不写入存储。
- 具体错误类型、外连接输出顺序和合并键取值仍待确认。

## Spec Patch

- 当前暂无新的 Spec Patch；已确认的范围已存在于 OpenSpec delta specs。
- 待确认：若技术方案发现现有 delta spec 对执行顺序、Row 标签、NATURAL 无共同列或 RIGHT/FULL 语义描述不足，将只补充边界场景，不改变能力范围。

## 待确认

- 多表外连接的稳定输出顺序契约。
- NATURAL JOIN 无共同列时的具体结果语义。
- USING/NATURAL 合并列的标签及左右值不一致时的取值策略。
- JOIN 名称解析错误使用独立错误类型还是现有 `ExecutionError` 子类。
