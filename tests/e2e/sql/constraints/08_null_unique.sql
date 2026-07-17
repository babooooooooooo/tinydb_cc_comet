CREATE TABLE u(id INT, email TEXT UNIQUE);
INSERT INTO u(id, email) VALUES (1, NULL);
INSERT INTO u(id, email) VALUES (2, NULL);
INSERT INTO u(id, email) VALUES (3, 'a@x');
INSERT INTO u(id, email) VALUES (4, 'a@x');
SELECT id FROM u;
