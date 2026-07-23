"""不可变 LogicalPlan 中间层 + build_plan。

设计依据：Design Doc §3.4 / §4.1 / §5.4。无副作用：不触碰 Pager / WAL /
Transaction，只读取 AST + Catalog 元数据并物化为 frozen dataclass 树。

Public API:
    7 个 frozen dataclass 节点（Scan / Join / Filter / Aggregate / Sort /
    Project / Limit）以及 Union 类型别名 ``LogicalPlan``。
    两个工厂入口：
      - ``build_plan(ast, catalog) -> LogicalPlan``
      - ``format_plan(plan) -> str``（稳定缩进文本，供 ``.explain`` 打印）
"""
from dataclasses import dataclass
from typing import Any, Optional

from tinydb.catalog import Catalog
from tinydb.parser import Select
from tinydb.resolver import resolve


# --- 节点 -------------------------------------------------------------------


@dataclass(frozen=True)
class Scan:
    """物理表扫描。``source_id`` 是 alias 优先、否则表名。"""

    table: str
    alias: Optional[str]
    schema: tuple
    source_id: str


@dataclass(frozen=True)
class Join:
    """两表等值/外连接节点（左深）。"""

    kind: str                       # INNER / LEFT / RIGHT / FULL / CROSS
    left: "LogicalPlan"
    right: "LogicalPlan"
    keys: tuple                      # tuple[JoinKey, ...]
    on_expr: Any                     # 已 fold 的表达式列表；CROSS / USING/NATURAL 时为空
    natural: bool = False


@dataclass(frozen=True)
class Filter:
    """WHERE 子句下推。"""

    source: "LogicalPlan"
    predicate: Any                   # 已 fold 的 Expr tuple（None / (op, ...)）


@dataclass(frozen=True)
class Aggregate:
    """GROUP BY + 聚合函数调用。Task 5 范围内仅构造空壳，具体填充留给 Task 6/8。"""

    source: "LogicalPlan"
    group_keys: tuple                # tuple[int, ...]
    aggregates: tuple                # tuple[AggregateCall, ...]


@dataclass(frozen=True)
class Sort:
    """ORDER BY 子句。"""

    source: "LogicalPlan"
    keys: tuple                      # tuple[tuple[int, bool], ...] — (列位置, DESC)


@dataclass(frozen=True)
class Project:
    """SELECT 子句投影。"""

    source: "LogicalPlan"
    items: tuple                     # tuple[tuple[str, Any], ...] — (label, expr)
    star: bool = False


@dataclass(frozen=True)
class Limit:
    """LIMIT / OFFSET 子句。"""

    source: "LogicalPlan"
    limit: Optional[int]
    offset: Optional[int]


# 7 个节点的并集 type alias（运行时用 isinstance 分发；Union 类型无法注入方法）。
LogicalPlan = Scan | Join | Filter | Aggregate | Sort | Project | Limit


# --- 构造 -------------------------------------------------------------------


def build_plan(ast: Select, catalog: Catalog) -> LogicalPlan:
    """从 ``Select`` AST + ``Catalog`` 构造不可变 LogicalPlan。

    步骤：resolve() → 构造 Scan → 左深 Join → Filter → Aggregate → Sort →
    Project → Limit。无副作用：不修改 catalog，不触碰 pager/WAL/transaction。
    """
    rp = resolve(ast, catalog)

    # 来源 → Scan 节点（按 JOIN 链顺序：sources[0] = FROM，sources[i+1] = joins[i].right）。
    scans = [
        Scan(
            table=s.table_name, alias=s.alias,
            schema=s.schema, source_id=s.source_id,
        )
        for s in rp.sources
    ]

    # 左深构造 Join 节点。
    # 每个 Join 节点只用自己那一层的 keys 和 on_expr（resolver 提供 per_join_* 字段）；
    # 这样 chained JOIN 时各层独立 evaluate 各自的 ON 谓词。
    current: LogicalPlan = scans[0]
    for i, (join_ast, scan) in enumerate(zip(ast.joins, scans[1:])):
        per_keys = rp.per_join_keys[i] if i < len(rp.per_join_keys) else ()
        per_on = rp.per_join_on_resolved[i] if i < len(rp.per_join_on_resolved) else None
        # 包装为 tuple-of-tuples 以便执行层 for pred in node.on_expr 正确迭代
        on_expr_for_node: Any = (per_on,) if per_on is not None else ()
        current = Join(
            kind=join_ast.kind, left=current, right=scan,
            keys=per_keys,
            on_expr=on_expr_for_node,
            natural=join_ast.natural,
        )

    # Filter
    if rp.where_resolved is not None:
        current = Filter(source=current, predicate=rp.where_resolved)

    # Aggregate（仅当 SELECT 命中聚合或 GROUP BY；Task 5 范围内仅构造空壳）。
    if ast.aggregate_aliases or ast.group_by:
        current = Aggregate(
            source=current,
            group_keys=tuple(),
            aggregates=tuple(),
        )

    # Sort
    if ast.order_by:
        keys = tuple(
            (_resolve_order_key(it, rp), it.descending)
            for it in ast.order_by
        )
        current = Sort(source=current, keys=keys)

    # Limit（在 Project 之前，便于提前截断排序后行数）。
    if ast.limit is not None or ast.offset is not None:
        current = Limit(source=current, limit=ast.limit, offset=ast.offset)

    # Project（顶层）
    items: list = []
    star = False
    if ast.select_items:
        for si in ast.select_items:
            if si.kind == "star":
                star = True
                items.append(("", "star"))
            elif si.kind == "column":
                items.append((si.alias or si.name, ("col", getattr(si, "qualifier", None), si.name)))
            elif si.kind == "aggregate":
                items.append((si.alias or "", ("agg", si.aggregate)))
    else:
        # legacy columns（v0.1 单表 SELECT id, name 路径）
        for c in ast.columns:
            items.append((c, ("col", None, c)))
    current = Project(source=current, items=tuple(items), star=star)

    return current


def _resolve_order_key(it, rp) -> int:
    """ORDER BY ``OrderByItem`` → merged schema 中的列位置。"""
    pos, _ = rp.column_resolver((getattr(it, "qualifier", None), it.column))
    return pos


# --- 格式（稳定缩进文本，供 .explain 打印） --------------------------------


_INDENT = "   "


def _format(node: LogicalPlan, depth: int = 0) -> str:
    pad = _INDENT * depth
    if isinstance(node, Scan):
        alias = f" AS {node.alias}" if node.alias else ""
        return f"{pad}Scan({node.table}{alias}, schema={node.schema})"
    if isinstance(node, Join):
        head = (
            f"{pad}Join({node.kind}, keys={len(node.keys)}"
            f"{', natural' if node.natural else ''})"
        )
        left = _format(node.left, depth + 1)
        right = _format(node.right, depth + 1)
        return f"{head}\n{left}\n{right}"
    if isinstance(node, Filter):
        return f"{pad}Filter\n" + _format(node.source, depth + 1)
    if isinstance(node, Aggregate):
        return (
            f"{pad}Aggregate(groups={len(node.group_keys)}, "
            f"funcs={len(node.aggregates)})\n"
            + _format(node.source, depth + 1)
        )
    if isinstance(node, Sort):
        return f"{pad}Sort(keys={node.keys})\n" + _format(node.source, depth + 1)
    if isinstance(node, Project):
        return (
            f"{pad}Project(items={len(node.items)}"
            f"{', star' if node.star else ''})\n"
            + _format(node.source, depth + 1)
        )
    if isinstance(node, Limit):
        return (
            f"{pad}Limit(limit={node.limit}, offset={node.offset})\n"
            + _format(node.source, depth + 1)
        )
    raise ValueError(f"unknown plan node: {type(node).__name__}")


def format_plan(plan: LogicalPlan) -> str:
    """稳定缩进文本输出。包含 ``Project`` / ``Join(INNER`` / ``Scan(...)`` 等关键字。"""
    return _format(plan)