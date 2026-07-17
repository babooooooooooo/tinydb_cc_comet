CREATE TABLE t(id INT, a INT, b TEXT);
INSERT INTO t(id, a, b) VALUES (5, 100, 'old');
INSERT INTO t(id, a, b) VALUES (6, 200, 'keep');
UPDATE t SET a=1, b='x' WHERE id=5;
SELECT * FROM t
