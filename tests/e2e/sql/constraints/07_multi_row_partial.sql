CREATE TABLE u(id INT PRIMARY KEY, name TEXT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b'), (1, 'c');
SELECT * FROM u;
