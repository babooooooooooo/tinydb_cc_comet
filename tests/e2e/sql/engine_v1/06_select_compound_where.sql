CREATE TABLE t(id INT, a INT, b INT, c INT);
INSERT INTO t(id, a, b, c) VALUES (1, 1, 2, 3);
INSERT INTO t(id, a, b, c) VALUES (2, 1, 99, 3);
INSERT INTO t(id, a, b, c) VALUES (3, 2, 2, 4);
INSERT INTO t(id, a, b, c) VALUES (4, 1, 2, 99);
SELECT * FROM t WHERE a=1 AND (b=2 OR c=3)
