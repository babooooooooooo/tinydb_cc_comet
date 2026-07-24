CREATE TABLE a(id INT);
CREATE TABLE b(id INT);
CREATE TABLE c(id INT);
INSERT INTO a(id) VALUES (1), (2);
INSERT INTO b(id) VALUES (1), (2);
INSERT INTO c(id) VALUES (1), (2);
SELECT a.id, b.id, c.id FROM a JOIN b ON a.id = b.id JOIN c ON b.id = c.id;