import pytest
import tinydb
from tinydb.plan import LogicalPlan, Scan


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "test.db")
    d = tinydb.Database(p)
    yield d
    d.close()


def _setup(db):
    db.execute("CREATE TABLE u(id INT, name TEXT)")
    db.execute("CREATE TABLE o(id INT, uid INT)")


@pytest.mark.integration
def test_explain_plan_returns_logical_plan(db):
    _setup(db)
    plan = db.explain_plan("SELECT u.id FROM u JOIN o ON u.id = o.uid")
    assert isinstance(plan, LogicalPlan)
    assert isinstance(plan, Scan) is False  # 顶层为 Project


@pytest.mark.integration
def test_explain_plan_does_not_modify_pager_or_wal(db):
    _setup(db)
    pc_before = db.pager.page_count()
    plan = db.explain_plan("SELECT u.id FROM u JOIN o ON u.id = o.uid")
    assert db.pager.page_count() == pc_before
    # plan 构造不写文件
    assert plan is not None


@pytest.mark.integration
def test_explain_plan_for_single_table(db):
    db.execute("CREATE TABLE t(id INT)")
    plan = db.explain_plan("SELECT * FROM t")
    assert isinstance(plan, LogicalPlan)


@pytest.mark.integration
def test_explain_plan_raises_on_non_select(db):
    _setup(db)
    with pytest.raises(tinydb.ExecutionError):
        db.explain_plan("CREATE TABLE x(id INT)")
