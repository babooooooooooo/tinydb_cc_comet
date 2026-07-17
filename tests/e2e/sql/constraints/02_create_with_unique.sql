CREATE TABLE u(id INT, email TEXT UNIQUE);
INSERT INTO u(id, email) VALUES (1, 'a@x');
INSERT INTO u(id, email) VALUES (2, 'b@x');
SELECT email FROM u;
