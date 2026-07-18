"""End-to-end round-trip tests for all 15 types wired through the executor.

Each test creates a table with a typed column, inserts a value, selects it back,
and asserts the decoded value matches the inserted one. Overflow/rejection
tests verify the codec contract surfaces errors at INSERT time.
"""
import datetime
import math
import pytest
from tinydb.database import Database


def test_create_and_insert_varchar():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, name VARCHAR(64))")
        db.execute("INSERT INTO t (id, name) VALUES (1, 'alice')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 1
        assert rows[0].name == "alice"


def test_create_and_insert_decimal():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, amount DECIMAL(10, 2))")
        db.execute("INSERT INTO t (id, amount) VALUES (1, 12.34)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].amount == 12.34


def test_create_and_insert_date():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, d DATE)")
        db.execute("INSERT INTO t (id, d) VALUES (1, DATE '2026-07-16')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].d == datetime.date(2026, 7, 16)


def test_create_and_insert_time():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, t TIME)")
        db.execute("INSERT INTO t (id, t) VALUES (1, TIME '14:30:00')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].t == datetime.time(14, 30, 0)


def test_create_and_insert_timestamp():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, ts TIMESTAMP)")
        db.execute("INSERT INTO t (id, ts) VALUES (1, TIMESTAMP '2026-07-16 14:30:00')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].ts == datetime.datetime(2026, 7, 16, 14, 30, 0)


def test_create_and_insert_smallint():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id SMALLINT)")
        db.execute("INSERT INTO t (id) VALUES (100)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 100


def test_create_and_insert_bigint():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id BIGINT)")
        db.execute("INSERT INTO t (id) VALUES (1000000000)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 1_000_000_000


def test_create_and_insert_double():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, val DOUBLE)")
        db.execute("INSERT INTO t (id, val) VALUES (1, 3.14159265358979)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].val == 3.14159265358979


def test_create_and_insert_real_alias():
    """REAL is alias for FLOAT (4-byte)."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, val REAL)")
        db.execute("INSERT INTO t (id, val) VALUES (1, 1.5)")
        rows = db.execute("SELECT * FROM t")
        assert abs(rows[0].val - 1.5) < 1e-6


def test_create_and_insert_boolean_alias():
    """BOOLEAN is alias for BOOL."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, flag BOOLEAN)")
        db.execute("INSERT INTO t (id, flag) VALUES (1, TRUE)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].flag is True


def test_create_and_insert_integer_alias():
    """INTEGER is alias for INT."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INTEGER)")
        db.execute("INSERT INTO t (id) VALUES (42)")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].id == 42


def test_create_and_insert_char_padded():
    """CHAR(5) right-pads 'ab' to 'ab   '."""
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id INT, code CHAR(5))")
        db.execute("INSERT INTO t (id, code) VALUES (1, 'ab')")
        rows = db.execute("SELECT * FROM t")
        assert rows[0].code == "ab   "


def test_varchar_overflow_raises():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (name VARCHAR(5))")
        with pytest.raises((TypeError, ValueError)):
            db.execute("INSERT INTO t (name) VALUES ('too long')")


def test_decimal_precision_overflow_raises():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (amount DECIMAL(5, 2))")
        with pytest.raises((OverflowError, ValueError)):
            db.execute("INSERT INTO t (amount) VALUES (12345.67)")


def test_int_overflow_raises():
    with Database(":memory:") as db:
        db.execute("CREATE TABLE t (id SMALLINT)")
        with pytest.raises((OverflowError, ValueError)):
            db.execute("INSERT INTO t (id) VALUES (1000000)")


def test_double_inf_rejected():
    """The DOUBLE codec rejects inf values at validate() time (executor INSERT step 6)."""
    from tinydb.type_system import codec_for
    codec = codec_for("DOUBLE")
    with pytest.raises((ValueError, OverflowError)):
        codec.validate(float("inf"))