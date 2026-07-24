CREATE TABLE u(id INT, name TEXT);
CREATE TABLE o(id INT, uid INT, total INT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b');
INSERT INTO o(id, uid, total) VALUES (10, 1, 100), (11, 2, 200), (12, 3, 50);
SELECT u.id, o.id FROM u INNER JOIN o ON u.id = o.uid;