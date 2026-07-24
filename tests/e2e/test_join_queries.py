"""golden SQL E2E for join-query change.

参考 tests/e2e/test_golden_sql.py 的目录约定（每个 .sql 文件一组预期输出），
本测试扫描 tests/e2e/sql/join/*.sql 并通过 Database.execute 跑出 Row 列表，
与同名的 .expected.txt 比对。
"""
import os
from pathlib import Path

import pytest

import tinydb

JOIN_SQL_DIR = Path(__file__).parent / "sql" / "join"


@pytest.mark.parametrize(
    "sql_path",
    sorted(JOIN_SQL_DIR.glob("*.sql")),
    ids=lambda p: p.name,
)
def test_join_golden(tmp_path, sql_path):
    expected_path = sql_path.with_suffix(".expected.txt")
    if not expected_path.exists():
        pytest.skip(f"no expected file for {sql_path.name}")

    d = tinydb.Database(str(tmp_path / f"{sql_path.stem}.db"))
    try:
        sql = sql_path.read_text()
        rows = d.execute(sql)
        actual = "\n".join(repr(r) for r in rows) + ("\n" if rows else "")
    finally:
        d.close()
    expected = expected_path.read_text()
    assert actual.strip() == expected.strip()