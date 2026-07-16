#!/usr/bin/env bash
set -euo pipefail

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
DB="$TMP_DIR/repl.db"
OUT="$TMP_DIR/repl.out"

printf '%s\n' \
  'CREATE TABLE t(id INT);' \
  'INSERT INTO t(id) VALUES (1);' \
  'SELECT * FROM t;' \
  '.exit' \
  | tinydb-repl --database "$DB" >"$OUT" 2>&1

test -f "$DB"
test "$(grep -c 'OK' "$OUT")" -eq 2
grep -Eq 'id[[:space:]]*' "$OUT"
grep -Eq '(^|[[:space:]])1([[:space:]]|$)' "$OUT"

echo "smoke: OK"