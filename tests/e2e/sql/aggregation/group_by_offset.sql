CREATE TABLE emp(id INT, dept TEXT);
INSERT INTO emp(id, dept) VALUES (1, 'eng');
INSERT INTO emp(id, dept) VALUES (2, 'hr');
INSERT INTO emp(id, dept) VALUES (3, 'sales');
SELECT dept, COUNT(*) AS n FROM emp GROUP BY dept ORDER BY dept LIMIT 1 OFFSET 1
