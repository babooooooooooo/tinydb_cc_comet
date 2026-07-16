CREATE TABLE t(id INT, name TEXT);
INSERT INTO t(id, name) VALUES (1, 'old');
DROP TABLE t;
CREATE TABLE t(id INT, name TEXT);
INSERT INTO t(id, name) VALUES (2, 'new');
SELECT * FROM t
