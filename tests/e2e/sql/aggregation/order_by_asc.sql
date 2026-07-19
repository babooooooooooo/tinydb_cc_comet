CREATE TABLE emp(id INT, dept TEXT);
INSERT INTO emp(id, dept) VALUES (1, 'sales');
INSERT INTO emp(id, dept) VALUES (2, 'eng');
INSERT INTO emp(id, dept) VALUES (3, 'hr');
SELECT dept, COUNT(*) FROM emp GROUP BY dept ORDER BY dept
