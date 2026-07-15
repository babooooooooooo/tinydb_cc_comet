# Spec: storage-engine

> 范围：MVP 阶段的单文件 slotted-page 存储引擎。WAL / 崩溃恢复属于 `tinydb-acid`；B-tree 索引属于 `tinydb-engine-v2`；并发安全永久 out。

## ADDED Requirements

### Requirement: Single-file .db format with magic header

The system SHALL persist tables into a single `.db` file identified by a fixed magic header on page 0. Opening an existing file MUST verify the magic header before any data access.

#### Scenario: Create new .db file writes magic header
- **WHEN** opening a non-existent file path with `Database(path)`
- **THEN** the system MUST create the file and write the magic bytes `b'TINYDB\\x00\\x01'` into page 0
- **AND** must also write the schema_version byte (`0x01` for MVP) into page 0 header

#### Scenario: Open existing .db verifies magic
- **WHEN** opening a file whose page 0 does not start with the magic bytes
- **THEN** the system SHALL raise `InvalidDatabaseFile` with a message indicating the file is not a tinydb file

#### Scenario: Reject wrong schema version
- **WHEN** opening a file with valid magic but unknown schema_version
- **THEN** the system SHALL raise `UnsupportedSchemaVersion` with the version number in the message

#### Scenario: Support `:memory:` mode
- **WHEN** opening `Database(':memory:')`
- **THEN** the system MUST NOT touch the filesystem
- **AND** must use an in-memory byte buffer as backing storage

### Requirement: Fixed 4KB page addressing

The system SHALL use a fixed page size of 4096 bytes. Page addressing SHALL be by integer id, with page 0 always being the file header page.

#### Scenario: Allocate a new page returns monotonic id
- **WHEN** calling `alloc_page()`
- **THEN** the returned page id SHALL be greater than any previously allocated id

#### Scenario: Read page by id returns exact 4096 bytes
- **WHEN** calling `read_page(page_id)`
- **THEN** the returned bytes MUST be exactly 4096 bytes long

#### Scenario: Write page updates on-disk content
- **WHEN** calling `write_page(page_id, data)` followed by `read_page(page_id)` after a flush
- **THEN** the read MUST return the written data

### Requirement: Slotted page layout

The system SHALL organize each table data page as a slotted page: a fixed-size page header, a slot directory grown from the start, a free space region in the middle, and the data area grown from the end.

#### Scenario: Insert row into empty page succeeds
- **WHEN** inserting the first row into an empty data page
- **THEN** the page MUST record one slot entry with the row's offset and length
- **AND** the free-space offset MUST move forward by the slot directory size

#### Scenario: Insert into full page raises PageFull
- **WHEN** attempting to insert a row whose encoded size exceeds the available free space
- **THEN** the slotted page MUST raise `PageFull`

#### Scenario: Update row in-place when slot space suffices
- **WHEN** updating an existing row with a new value of the same or smaller encoded length
- **THEN** the slot's length SHALL be updated in place without moving the row

#### Scenario: Mark row deleted via tombstone
- **WHEN** deleting a row
- **THEN** the slot SHALL be marked as tombstoned (offset == 0xFFFF)
- **AND** the underlying data bytes MAY remain in place

#### Scenario: Reuse tombstoned slot on next insert
- **WHEN** inserting a row into a page that has a tombstoned slot
- **THEN** the slotted page SHALL reuse the tombstoned slot if the new row fits the freed length

### Requirement: Row encoding with null bitmap

The system SHALL encode each row as a null bitmap followed by length-prefixed column values. The null bitmap SHALL have one bit per column, LSB-first.

#### Scenario: Encode row with all non-null columns
- **WHEN** encoding `(42, 'alice', TRUE)` for schema `(INT, TEXT, BOOL)`
- **THEN** the bytes MUST start with `b'\\x00'` (no NULLs)
- **AND** followed by INT encoding + length-prefixed text + BOOL encoding

#### Scenario: Encode row with null in second column
- **WHEN** encoding `(42, NULL, FALSE)` for schema `(INT, TEXT, BOOL)`
- **THEN** the bytes MUST start with `b'\\x02'` (bit 1 set indicates TEXT is NULL)

#### Scenario: Decode row roundtrips with null
- **WHEN** decoding the encoded bytes of a row with one null column
- **THEN** the decoded list MUST contain `None` at the null column's index

### Requirement: Catalog at page 1

The system SHALL persist the catalog (table name to schema + root_page_id) as JSON on page 1. The catalog MUST be reloaded on Database open.

#### Scenario: Register new table updates catalog
- **WHEN** executing `CREATE TABLE t(id INT)`
- **THEN** the catalog on page 1 MUST contain an entry for `t` with its schema and allocated root page id

#### Scenario: Catalog persisted across reopen
- **WHEN** creating a table, closing the Database, and reopening the same file
- **THEN** the reopened Database MUST recognize the table

#### Scenario: Drop table removes from catalog and frees root page
- **WHEN** executing `DROP TABLE t`
- **THEN** the catalog MUST no longer contain `t`
- **AND** the root page id MUST be added to a free-page list (best-effort; MVP may just leak and recover at next alloc)

### Requirement: Row CRUD executor operations

The executor SHALL provide the following operations against the storage engine, accessible via `Executor.run(stmt)`.

#### Scenario: Full table scan returns all non-deleted rows
- **WHEN** scanning a table with three rows where one is tombstoned
- **THEN** the result MUST contain exactly two rows

#### Scenario: Equality filter on indexed-style scan
- **WHEN** executing `SELECT * FROM t WHERE col = 42`
- **THEN** the executor MUST use the storage engine's linear scan with predicate evaluation per row
- **AND** MUST return only rows whose `col` value equals `42`

#### Scenario: Insert row assigns new slot in next page
- **WHEN** inserting a row when the current page is full
- **THEN** the executor MUST allocate a new page
- **AND** the row MUST be inserted into the new page

### Requirement: MVP makes no crash-safety guarantee

The system SHALL document clearly that MVP makes no guarantees about surviving process crashes. Any partially-written state on crash MAY be lost.

#### Scenario: Docstring declares non-ACID semantics
- **WHEN** reading the `Database.__init__` docstring
- **THEN** the docstring MUST contain the phrase `"non-ACID, no crash safety"`

### Requirement: Overflow row spans multiple pages

The system SHALL allow rows whose encoded size exceeds MAX_INLINE_PAYLOAD (~3970 bytes) by storing them across a chain of pages. The first page's slot SHALL be marked SPILL_START and its page header `overflow_next_page_id` points to the next overflow page. The chain SHALL terminate with `overflow_next_page_id = 0xFFFFFFFF` (NULL_PAGE_ID).

#### Scenario: Insert row larger than MAX_INLINE_PAYLOAD spills across pages
- **WHEN** inserting a row whose encoded size exceeds MAX_INLINE_PAYLOAD
- **THEN** the first chunk (≤ MAX_INLINE_PAYLOAD bytes) SHALL be written into the current data page with slot flag SPILL_START set
- **AND** remaining chunks SHALL be written into newly allocated overflow pages (page_type=2) chained via `overflow_next_page_id`

#### Scenario: Read spill-start slot reconstructs full row bytes
- **WHEN** reading back a slot whose flags include SPILL_START
- **THEN** the executor SHALL follow the overflow_next_page_id chain
- **AND** concatenate the data area content of each overflow page
- **AND** return the fully reconstructed row bytes prior to type decoding

#### Scenario: Delete spill-start row frees overflow chain
- **WHEN** deleting a row whose slot is marked SPILL_START
- **THEN** the slot SHALL be marked TOMBSTONE
- **AND** every overflow page in the chain SHALL be marked page_type=0 (free) and returned to the Pager's free-page pool

### Requirement: Catalog schema encoded as JSON with INT-as-string

The system SHALL persist the catalog on page 1 using JSON. To defend against JSON's int precision limit (2^53), any integer-valued schema field whose value may exceed 2^53 SHALL be encoded as a JSON-quoted string.

#### Scenario: Catalog encodes INT schema fields as quoted strings
- **WHEN** the catalog serializes a schema field containing an integer that may exceed 2^53
- **THEN** the JSON output MUST encode that field as a string (e.g., `"9223372036854775807"`)
- **AND** the deserializer MUST convert the string back to `int`
