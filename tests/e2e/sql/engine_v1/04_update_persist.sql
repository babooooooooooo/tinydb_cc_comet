CREATE TABLE t(id INT, v TEXT);
INSERT INTO t(id, v) VALUES (1, 'first');
UPDATE t SET v='updated' WHERE id=1;
-- REOPEN
SELECT * FROM t
