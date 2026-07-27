"""结果格式化 — table / csv / json 三种输出格式."""
from __future__ import annotations

import csv
import io
import json
from typing import Literal

from tinydb.database import Row


MAX_COLUMN_WIDTH = 30

FormatName = Literal["table", "csv", "json"]
_VALID_FORMATS: tuple[FormatName, ...] = ("table", "csv", "json")


def format_rows(rows: list[Row], fmt: str) -> str:
    """按 fmt 把 rows 格式化为字符串.

    空 rows 统一返回 '(no rows)'.fmt ∈ {'table','csv','json'};未知 fmt 抛 ValueError.
    """
    if not rows:
        return "(no rows)"
    if fmt == "table":
        return _format_table(rows)
    if fmt == "csv":
        return _format_csv(rows)
    if fmt == "json":
        return _format_json(rows)
    raise ValueError(f"unknown format: {fmt}; expected one of {_VALID_FORMATS}")


def _format_table(rows: list[Row]) -> str:
    """迁移自 repl._format_table,字节级一致."""
    if not rows:
        return "(no rows)"
    columns = list(rows[0].columns)
    raw_values = [[str(value) for value in row.values] for row in rows]
    widths = [
        min(
            max(len(column), *(len(values[index]) for values in raw_values)),
            MAX_COLUMN_WIDTH,
        )
        for index, column in enumerate(columns)
    ]

    def truncate(value: str) -> str:
        if len(value) <= MAX_COLUMN_WIDTH:
            return value
        return value[: MAX_COLUMN_WIDTH - 1] + "…"

    def render(values: list[str]) -> str:
        cells = [truncate(value).ljust(width) for value, width in zip(values, widths)]
        return " | ".join(cells).rstrip()

    header = render(columns)
    separator = " | ".join("---" for _ in columns)
    body = [render(values) for values in raw_values]
    return "\n".join([header, separator, *body])


def _format_csv(rows: list[Row]) -> str:
    """RFC 4180 CSV.首行 header."""
    buf = io.StringIO()
    columns = list(rows[0].columns)
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(list(row.values))
    return buf.getvalue().rstrip("\n")


def _format_json(rows: list[Row]) -> str:
    """JSON 数组,每个元素是 dict[column]=value.

    values 使用 json 默认序列化;非 JSON-serializable 经 str() 降级.
    """
    columns = list(rows[0].columns)
    return json.dumps(
        [dict(zip(columns, row.values)) for row in rows],
        ensure_ascii=False,
        default=str,
    )
