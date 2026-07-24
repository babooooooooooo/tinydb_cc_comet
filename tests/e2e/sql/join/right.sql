CREATE TABLE u(id INT);
CREATE TABLE o(id INT, uid INT);
INSERT INTO u(id) VALUES (1), (2);
INSERT INTO o(id, uid) VALUES (10, 1), (11, 99);
SELECT u.id, o.id FROM u RIGHT JOIN o ON u.id = o.uid;