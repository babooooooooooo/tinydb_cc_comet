CREATE TABLE t(id INT, a INT, b INT);
INSERT INTO t(id, a, b) VALUES (1, 1, 3);
INSERT INTO t(id, a, b) VALUES (2, 2, 1);
INSERT INTO t(id, a, b) VALUES (3, 1, 1);
INSERT INTO t(id, a, b) VALUES (4, 2, 3);
INSERT INTO t(id, a, b) VALUES (5, 1, 2);
SELECT * FROM t ORDER BY a ASC, b DESC
