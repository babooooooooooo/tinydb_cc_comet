CREATE TABLE users(id INT, age INT);
INSERT INTO users(id, age) VALUES (1, 30);
INSERT INTO users(id, age) VALUES (2, 25);
SELECT COUNT(age) FROM users
