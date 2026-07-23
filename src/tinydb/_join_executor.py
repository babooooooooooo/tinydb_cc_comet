"""JOIN 执行：nested-loop INNER / CROSS baseline（Task 6）。

设计依据：Design Doc §3.2 / §4.1 / §4.2 / §5.3。事务读路由由 executor 负责，
本模块仅消费 plan 树并在必要时调 ``executor._scan_table``。LEFT/RIGHT/FULL/
NATURAL outer、复合 ON、GROUP BY 关联等扩展留待后续 task。

ON 谓词的位置语义：resolver 折叠后的 ``on_expr`` 是 ``(op, lpos, rpos, l_src_id,
r_src_id)``，其中 ``lpos`` / ``rpos`` 是 source-local 位置（在该 source 的 schema
中）。chained JOIN 时 left 子树是复合的，因此执行层需把 source-local 位置 remap
到 subtree-local 位置 —— 实现见 :meth:`_remap_position`。
"""
from typing import Any

from tinydb.catalog import TableInfo
from tinydb.plan import (
    LogicalPlan, Scan, Join, Filter, Aggregate, Sort, Project, Limit,
)


# 行类型：list[Any]；schema 类型：list[str]
Row = list
Schema = list


class JoinExecutor:
    """对 plan 中 Join 节点执行 nested-loop；返回 ``(rows, output_schema)``。

    本类在调用方 ``executor._scan_table(ti)`` 处消费行，因此 ACID 读路径仍走
    ``executor._txn_read_page``。
    """

    def __init__(self, executor) -> None:
        self.executor = executor

    # --- public ------------------------------------------------------------

    def execute_plan(self, plan: LogicalPlan) -> tuple[list, list]:
        """走 plan 树直到顶层 Project；返回 ``(rows, output_schema)``。"""
        rows, schema = self._eval(plan)
        return rows, schema

    # --- dispatch ----------------------------------------------------------

    def _eval(self, node: LogicalPlan) -> tuple[list, list]:
        if isinstance(node, Scan):
            return self._eval_scan(node)
        if isinstance(node, Join):
            return self._eval_join(node)
        if isinstance(node, Filter):
            return self._eval_filter(node)
        if isinstance(node, Project):
            return self._eval_project(node)
        if isinstance(node, Limit):
            return self._eval_limit(node)
        if isinstance(node, Sort):
            return self._eval_sort(node)
        if isinstance(node, Aggregate):
            return self._eval_aggregate(node)
        raise ValueError(f"unsupported plan node: {type(node).__name__}")

    # --- Scan --------------------------------------------------------------

    def _eval_scan(self, node: Scan) -> tuple[list, list]:
        ti: TableInfo = self.executor.catalog.get_table(node.table)
        rows: list = []
        for _sid, vals, _pid in self.executor._scan_table(ti):
            rows.append(list(vals))
        # 把列名加 source_id 前缀，使 Join 输出 schema 不会有同名列歧义。
        schema = [f"{node.source_id}.{c}" for c in node.schema]
        return rows, schema

    # --- Join --------------------------------------------------------------

    def _eval_join(self, node: Join) -> tuple[list, list]:
        left_rows, left_schema = self._eval(node.left)
        right_rows, right_schema = self._eval(node.right)

        if node.kind == "CROSS":
            return self._nested_loop_cross(
                left_rows, right_rows, left_schema, right_schema,
            )

        return self._nested_loop_inner(
            left_rows, left_schema, right_rows, right_schema, node,
        )

    def _collect_sources(self, node: LogicalPlan) -> list[tuple[str, tuple]]:
        """递归收集子树下所有 Scan 节点的 ``(source_id, schema)``，按构造顺序。

        Scan 顺序与 left-deep Join 构造顺序一致，因此 ``source_id`` 在子树
        schema 中的位置 = 前面所有 source 的 schema 长度累加。
        """
        if isinstance(node, Scan):
            return [(node.source_id, node.schema)]
        if isinstance(node, Join):
            return self._collect_sources(node.left) + self._collect_sources(node.right)
        # Filter / Project / Sort / Limit / Aggregate 不改变列布局。
        if hasattr(node, "source") and isinstance(node.source, LogicalPlan):
            return self._collect_sources(node.source)
        raise ValueError(f"cannot collect sources from {type(node).__name__}")

    def _build_source_position_map(
        self,
        subtree_node: LogicalPlan,
        subtree_schema: Schema,
    ) -> dict[str, tuple[int, int]]:
        """返回 ``source_id -> (offset_in_subtree_schema, source_schema_length)``。

        ``offset`` 是该 source 的第一列在 ``subtree_schema`` 中的位置；source
        的 source-local 位置 ``p`` → subtree-local 位置 ``offset + p``。

        USING/NATURAL 合并键的去重已经反映在 ``_coalesce_row`` 的输出布局里：
        合并键只在 left_schema 出现一次，right 的合并键位置被跳过。因此当一个
        source 整体是 USING 合并键右侧源时，它的全部列可能不出现在
        ``subtree_schema`` —— 但 ON 谓词不能引用 USING 合并键（应走 keys 路径），
        实际不会发生，所以简化处理为 offset = 子树前累加。
        """
        sources = self._collect_sources(subtree_node)
        mapping: dict[str, tuple[int, int]] = {}
        offset = 0
        for sid, sch in sources:
            mapping[sid] = (offset, len(sch))
            offset += len(sch)
        return mapping

    def _qualify_schema(
        self,
        schema: Schema,
        node: LogicalPlan,
    ) -> Schema:
        """对 Join 子树的 schema 加 source_id 前缀，使同名列可以区分。

        例如 ``users u JOIN orders o ON u.id = o.user_id`` 的合并 schema 由
        ``('id', 'name', 'id', 'user_id', 'total')`` 变为
        ``('u.id', 'u.name', 'o.id', 'o.user_id', 'o.total')``。USING/NATURAL
        合并键仍走 ``_merged_schema`` 去重，不进入此路径。
        """
        sources = self._collect_sources(node)
        out: list = []
        for sid, sch in sources:
            for col in sch:
                out.append(f"{sid}.{col}")
        return out

    def _merged_schema(
        self,
        left_schema: Schema,
        right_schema: Schema,
        node: Join,
    ) -> Schema:
        """输出 schema：USING/NATURAL 合并键只出现一次（用 merge label）；其余列保持原名。

        子树 schema 已经按 source_id 加前缀（如 ``'u.id'`` / ``'o.id'``），因此
        同名列可以区分。USING merge 把两侧的合并键列统一为单个 ``k.label``（如
        ``'id'``）；其余列按左在前、右在后的顺序输出。
        """
        out: list = []
        # 处理 left 中的列：合并键保留为 label，非合并键保留 qualified 名
        merge_pairs = {(k.left_col, k.right_col, k.label) for k in node.keys}
        merge_left_labels = {k.label for k in node.keys}
        merge_left_idx = {k.left_col: k.label for k in node.keys}
        for i, col in enumerate(left_schema):
            if i in merge_left_idx:
                out.append(merge_left_idx[i])
            else:
                out.append(col)
        # 处理 right 中的列：合并键位置跳过
        merge_right_idx = {k.right_col for k in node.keys}
        for i, col in enumerate(right_schema):
            if i in merge_right_idx:
                continue
            out.append(col)
        return out

    def _coalesce_row(
        self,
        left_row: Row,
        right_row: Row,
        node: Join,
        left_schema: Schema,
        right_schema: Schema,
    ) -> Row:
        """构造输出行：USING 合并键 Coalesce（left 非 NULL 优先；left 为 NULL 时用 right）。

        输出布局：left_row 在前，right_row 中非合并键位置追加在尾；合并键（按
        ``k.right_col``）跳过。
        """
        out = list(left_row)
        merge_right_idx = {k.right_col for k in node.keys}
        merge_left_idx_to_label = {k.left_col: k.label for k in node.keys}
        for ri, _col in enumerate(right_schema):
            if ri in merge_right_idx:
                # 找到对应的 left 位置（merge label 在 left 中的位置）。
                # left_schema 是 qualified schema，但 merge label 是 unqualified；
                # 此处用 left_schema 索引位置（与 _merged_schema 一致）。
                # Coalesce: left 为 None 时取 right
                li = next(
                    li for li, label in merge_left_idx_to_label.items()
                    if label == next(
                        k.label for k in node.keys if k.right_col == ri
                    )
                )
                if out[li] is None and right_row[ri] is not None:
                    out[li] = right_row[ri]
                continue
            out.append(right_row[ri])
        return out

    def _nested_loop_cross(
        self,
        left_rows: list,
        right_rows: list,
        left_schema: Schema,
        right_schema: Schema,
    ) -> tuple[list, list]:
        out_rows: list = []
        for lr in left_rows:
            for rr in right_rows:
                out_rows.append(list(lr) + list(rr))
        return out_rows, list(left_schema) + list(right_schema)

    def _nested_loop_inner(
        self,
        left_rows: list,
        left_schema: Schema,
        right_rows: list,
        right_schema: Schema,
        node: Join,
    ) -> tuple[list, list]:
        out_rows: list = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        # 构造 source_id → subtree-local offset 映射，用于 ON 谓词 remap。
        left_pos_map = self._build_source_position_map(node.left, left_schema)
        right_pos_map = self._build_source_position_map(node.right, right_schema)
        for lr in left_rows:
            for rr in right_rows:
                if self._matches(lr, rr, left_schema, right_schema, node,
                                  left_pos_map, right_pos_map):
                    out_rows.append(
                        self._coalesce_row(lr, rr, node, left_schema, right_schema),
                    )
        return out_rows, out_schema

    def _matches(
        self,
        lr: Row,
        rr: Row,
        ls: Schema,
        rs: Schema,
        node: Join,
        left_pos_map: dict,
        right_pos_map: dict,
    ) -> bool:
        """判断左右行是否匹配。优先 USING/NATURAL 合并键；否则用 ON 表达式。"""
        if node.keys:
            for k in node.keys:
                lv = lr[k.left_col]
                rv = rr[k.right_col]
                if lv is None or rv is None:
                    return False
                if lv != rv:
                    return False
            return True
        if node.on_expr:
            for pred in node.on_expr:
                if not self._eval_on(pred, lr, rr, ls, rs, left_pos_map, right_pos_map):
                    return False
            return True
        return True

    def _eval_on(
        self,
        on: Any,
        lr: Row,
        rr: Row,
        ls: Schema,
        rs: Schema,
        left_pos_map: dict,
        right_pos_map: dict,
    ) -> bool:
        """单层 ``(op, lpos, rpos, l_src_id, r_src_id)`` 谓词。"""
        if not (isinstance(on, tuple) and len(on) == 5 and on[0] == "="):
            return True
        _, lpos, rpos, l_src_id, r_src_id = on
        li = self._remap_position(l_src_id, lpos, left_pos_map, ls)
        ri = self._remap_position(r_src_id, rpos, right_pos_map, rs)
        return lr[li] == rr[ri]

    def _remap_position(
        self,
        source_id: str,
        source_pos: int,
        pos_map: dict,
        subtree_schema: Schema,
    ) -> int:
        """source-local 位置 → subtree-local 位置。

        ``pos_map`` 是当前子树（left 或 right）的 source_id → (offset, len)
        映射。subtree-local = offset + source_pos。
        """
        if source_id not in pos_map:
            raise KeyError(
                f"source_id {source_id!r} not found in subtree sources "
                f"(known: {sorted(pos_map)})",
            )
        offset, _length = pos_map[source_id]
        return offset + source_pos

    # --- Filter / Project / Limit / Sort / Aggregate -----------------------

    def _eval_filter(self, node: Filter) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        pred = node.predicate
        if pred is None:
            return rows, schema
        out: list = []
        for r in rows:
            if self._eval_predicate(r, pred, schema):
                out.append(r)
        return out, schema

    def _eval_predicate(self, row: Row, pred: Any, schema: Schema) -> bool:
        """折叠后谓词求值。WHERE 形式由 resolver 折叠为：
        - ``(column, '=', pos, value)``：列对字面量等值
        - ``('AND', left, right)`` / ``('OR', left, right)`` / ``('NOT', operand)``
        """
        if isinstance(pred, tuple) and len(pred) == 4 and pred[1] == "=":
            _col, _op, pos, value = pred
            return row[pos] == value
        if isinstance(pred, tuple) and len(pred) == 3:
            if pred[0] == "AND":
                return (
                    self._eval_predicate(row, pred[1], schema)
                    and self._eval_predicate(row, pred[2], schema)
                )
            if pred[0] == "OR":
                return (
                    self._eval_predicate(row, pred[1], schema)
                    or self._eval_predicate(row, pred[2], schema)
                )
            if pred[0] == "NOT":
                return not self._eval_predicate(row, pred[1], schema)
        return True

    def _eval_project(self, node: Project) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        if node.star:
            return rows, schema
        out_rows: list = []
        # label 优先取显式 alias；alias 含 '.' 或 qualifier 非空时升级为限定名。
        new_labels: list = []
        for label, expr in node.items:
            if label and "." in label:
                new_labels.append(label)
                continue
            if isinstance(expr, tuple) and len(expr) == 3 and expr[0] == "col":
                _tag, qualifier, name = expr
                if qualifier:
                    new_labels.append(f"{qualifier}.{name}")
                    continue
                new_labels.append(label or name)
                continue
            new_labels.append(label or "")
        for r in rows:
            projected = []
            for _label, expr in node.items:
                value = self._project_expr(expr, r, schema)
                projected.append(value)
            out_rows.append(projected)
        return out_rows, new_labels

    def _project_expr(self, expr: Any, row: Row, schema: Schema) -> Any:
        """Project 子项求值：``("col", qualifier, name)`` / ``"star"`` / ``("agg", ...)``。

        子树 schema 已 qualified（如 ``'u.id'``）；当 ``qualifier`` 非空时优先
        按 ``qualifier.name`` 精确匹配，否则 fallback 到 unqualified ``name``。
        """
        if expr == "star":
            return None
        if isinstance(expr, tuple) and len(expr) == 3 and expr[0] == "col":
            _tag, qualifier, name = expr
            if qualifier:
                qualified = f"{qualifier}.{name}"
                if qualified in schema:
                    return row[schema.index(qualified)]
            if name in schema:
                return row[schema.index(name)]
            return None
        if isinstance(expr, tuple) and len(expr) == 2 and expr[0] == "agg":
            return None
        return None

    def _eval_limit(self, node: Limit) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        if node.offset:
            rows = rows[node.offset:]
        if node.limit is not None:
            rows = rows[: node.limit]
        return rows, schema

    def _eval_sort(self, node: Sort) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        keys = node.keys
        for idx, desc in reversed(keys):
            rows.sort(
                key=lambda r: (r[idx] is None, r[idx]),
                reverse=bool(desc),
            )
        return rows, schema

    def _eval_aggregate(self, node: Aggregate) -> tuple[list, list]:
        """Task 6 baseline：JOIN 树上方挂 Aggregate = fallback 到 source 求值（无聚合）。"""
        return self._eval(node.source)