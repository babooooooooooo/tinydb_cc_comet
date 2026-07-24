CREATE TABLE u(id INT, name TEXT);
CREATE TABLE o(id INT, uid INT);
INSERT INTO u(id, name) VALUES (1, 'a'), (2, 'b'), (3, 'c');
INSERT INTO o(id, uid) VALUES (10, 1), (11, 2);
SELECT u.id, o.id FROM u LEFT JOIN o ON u.id = o.uid;