CREATE TABLE u(id INT, name TEXT);
CREATE TABLE o(id INT, total INT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b');
INSERT INTO o(id, total) VALUES (1, 100), (3, 50);
SELECT * FROM u JOIN o USING (id);