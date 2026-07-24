CREATE TABLE u(id INT, name TEXT, dept TEXT);
CREATE TABLE o(id INT, total INT);
INSERT INTO u(id, name, dept) VALUES (1, 'a', 'eng'), (2, 'b', 'sales');
INSERT INTO o(id, total) VALUES (1, 100), (3, 50);
SELECT * FROM u NATURAL INNER JOIN o;