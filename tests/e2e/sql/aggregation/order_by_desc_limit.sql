CREATE TABLE emp(id INT, dept TEXT);
INSERT INTO emp(id, dept) VALUES (1, 'sales');
INSERT INTO emp(id, dept) VALUES (2, 'sales');
INSERT INTO emp(id, dept) VALUES (3, 'eng');
INSERT INTO emp(id, dept) VALUES (4, 'hr');
INSERT INTO emp(id, dept) VALUES (5, 'hr');
INSERT INTO emp(id, dept) VALUES (6, 'hr');
SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept ORDER BY n DESC LIMIT 1
