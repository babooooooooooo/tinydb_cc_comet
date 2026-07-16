CREATE TABLE users(id INT, name TEXT);
INSERT INTO users(id, name) VALUES (1, 'alice');
INSERT INTO users(id, name) VALUES (2, 'bob');
INSERT INTO users(id, name) VALUES (3, 'carol');
DELETE FROM users WHERE id = 2;
SELECT * FROM users
