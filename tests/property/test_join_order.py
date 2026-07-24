"""Property: LEFT/RIGHT/FULL 输出顺序 = strict-left-deep-insertion。

对生成的两表/三表查询与数据，断言：
- LEFT 未匹配行紧跟其左行；
- RIGHT/FULL 右未匹配行追加在末尾，且按右表扫描顺序。
"""
import pytest
import tinydb


def _build_db(tmp_path, schema):
    p = str(tmp_path / "ord.db")
    d = tinydb.Database(p)
    for name, cols in schema.items():
        d.execute(f"CREATE TABLE {name}({', '.join(cols)})")
    return d


@pytest.mark.property
def test_left_join_strict_order_with_random_data(tmp_path):
    d = _build_db(tmp_path, {"u": ["id INT", "name TEXT"], "o": ["id INT", "uid INT"]})
    # 左表 5 行，右表 7 行
    d.execute("INSERT INTO u(id, name) VALUES (1,'a'),(2,'b'),(3,'c'),(4,'d'),(5,'e')")
    d.execute(
        "INSERT INTO o(id, uid) VALUES "
        "(1,1),(2,1),(3,2),(4,3),(5,3),(6,99),(7,100)"
    )
    rows = d.execute(
        "SELECT u.id, o.id FROM u LEFT JOIN o ON u.id = o.uid"
    )
    # 期望顺序：
    # u=1: o=1, o=2
    # u=2: o=3
    # u=3: o=4, o=5
    # u=4: NULL
    # u=5: NULL
    expected_u_seq = [1, 1, 2, 3, 3, 4, 5]
    expected_o_seq = [1, 2, 3, 4, 5, None, None]
    actual_u_seq = [r["u.id"] for r in rows]
    actual_o_seq = [r["o.id"] for r in rows]
    assert actual_u_seq == expected_u_seq
    assert actual_o_seq == expected_o_seq


@pytest.mark.property
def test_full_join_unmatched_right_appended_in_scan_order(tmp_path):
    d = _build_db(tmp_path, {"u": ["id INT"], "o": ["id INT", "uid INT"]})
    d.execute("INSERT INTO u(id) VALUES (1), (2)")
    d.execute("INSERT INTO o(id, uid) VALUES (10, 1), (11, 99), (12, 100)")
    rows = d.execute(
        "SELECT u.id, o.id FROM u FULL JOIN o ON u.id = o.uid"
    )
    # 顺序：匹配 (u=1,o=10) + 左未匹配 (u=2,NULL) + 右未匹配 (NULL,o=11), (NULL,o=12)
    actual = [(r["u.id"], r["o.id"]) for r in rows]
    assert actual == [
        (1, 10),
        (2, None),
        (None, 11),
        (None, 12),
    ]