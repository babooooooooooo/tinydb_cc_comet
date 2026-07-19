CREATE TABLE emp(id INT, dept TEXT);
INSERT INTO emp(id, dept) VALUES (1, 'eng');
INSERT INTO emp(id, dept) VALUES (2, 'eng');
INSERT INTO emp(id, dept) VALUES (3, 'sales');
INSERT INTO emp(id, dept) VALUES (4, 'sales');
INSERT INTO emp(id, dept) VALUES (5, 'sales');
SELECT dept, COUNT(*) FROM emp GROUP BY dept
