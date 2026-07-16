CREATE TABLE users(id INT, name TEXT);
INSERT INTO users(id, name) VALUES (1, 'alice');
INSERT INTO users(id, name) VALUES (2, 'bob');
-- REOPEN
SELECT * FROM users
