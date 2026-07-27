## ADDED Requirements

### Requirement: Database constructor accepts locking flag

The system SHALL accept an optional `locking: bool = True` keyword argument in `Database.__init__`. When `locking=True` (default), the system MUST acquire both an in-process reentrant lock (`threading.RLock`) on the `Database` instance and an exclusive OS-level file lock on the underlying DB file when the path is not `:memory:`. When `locking=False`, the system MUST NOT acquire any lock and MUST behave as a single-threaded database with zero locking overhead.

#### Scenario: Default constructor enables locking
- **WHEN** calling `Database("/path/to/file.db")` with no `locking` argument
- **THEN** the system MUST acquire `threading.RLock` on the instance
- **AND** MUST acquire `fcntl.flock(LOCK_EX)` on the open DB file fd
- **AND** MUST raise `DatabaseLocked` if another process holds the lock on the same file

#### Scenario: Explicit opt-out disables locking
- **WHEN** calling `Database("/path/to/file.db", locking=False)`
- **THEN** the system MUST NOT acquire `threading.RLock`
- **AND** MUST NOT acquire `fcntl.flock`
- **AND** MUST succeed even if another process holds the DB lock

#### Scenario: In-memory database skips file lock
- **WHEN** calling `Database(":memory:", locking=True)` (or with default)
- **THEN** the system MUST acquire `threading.RLock` on the instance
- **AND** MUST NOT call `fcntl.flock` (no file exists)

### Requirement: Coarse-grained thread serialization at execute boundary

The system SHALL serialize concurrent calls to `Database.execute()` and `Database.explain_plan()` on the same instance via the per-instance `threading.RLock`. The lock MUST be acquired before tokenization begins and released after the executor returns. Reentrant calls from within the locked region (e.g., a method that internally invokes another locked method) MUST be allowed.

#### Scenario: Two threads executing concurrent INSERTs are serialized
- **WHEN** two threads on the same `Database` instance invoke `execute("INSERT ...")` simultaneously
- **THEN** the two calls SHALL NOT overlap their critical sections
- **AND** the final committed row count SHALL equal the sum of both inserts (no lost writes)

#### Scenario: Reentrant call from within locked region does not deadlock
- **WHEN** a helper method invoked inside `execute()` calls another method on the same `Database` that also acquires the lock
- **THEN** the system MUST NOT deadlock (RLock is reentrant)

### Requirement: Cross-process exclusive lock via fcntl

For non-`:memory:` paths, the system SHALL acquire `fcntl.flock(LOCK_EX)` on the Pager's file descriptor during `Pager.__init__`. The lock MUST be released when `Pager.close()` is called or when the process exits (OS-level automatic release on fd close). A second process attempting to open the same DB file while the first holds the lock MUST raise `DatabaseLocked` within 100 ms.

#### Scenario: Second process open raises DatabaseLocked
- **WHEN** process A holds an exclusive lock on `/tmp/x.db` and process B opens `/tmp/x.db` with `Database("/tmp/x.db")`
- **THEN** process B's `Database.__init__` MUST raise `DatabaseLocked` indicating `/tmp/x.db` is locked by another process

#### Scenario: Closing the first process frees the lock for the second
- **WHEN** process A closes its `Database` (or crashes) and process B retries the open
- **THEN** process B MUST successfully acquire the lock and complete `Database.__init__`

#### Scenario: In-memory mode does not call fcntl
- **WHEN** `Database(":memory:")` is opened
- **THEN** `Pager.__init__` MUST NOT call `fcntl.flock` (skipping file lock entirely)

### Requirement: Recovery replay cooperates with file lock

When `Pager.__init__` triggers `Recovery.replay()` because a non-empty WAL exists, the replay MUST run while the file lock is held. A concurrent process attempting to open the same DB during replay MUST observe `DatabaseLocked` until replay completes.

#### Scenario: Replay blocks competing opener
- **WHEN** process A opens a DB with a non-empty WAL and process B opens the same DB before A's `Pager.__init__` returns
- **THEN** process B MUST observe `DatabaseLocked` (the flock is held across the replay call)

### Requirement: Lock acquisition failure is observable

The system SHALL raise `tinydb.errors.DatabaseLocked` (a subclass of `TinydbError`) when an exclusive lock cannot be acquired. The exception message MUST include the DB file path.

#### Scenario: Lock failure raises DatabaseLocked with path
- **WHEN** `Pager.__init__` calls `fcntl.flock(LOCK_EX)` and it returns -1 with `EWOULDBLOCK`
- **THEN** the system MUST raise `DatabaseLocked` whose message includes the file path

### Requirement: Close releases all locks

`Database.close()` SHALL release the `threading.RLock` (semantic — `RLock` cannot be force-released, but the lock state is reset to released) and release the `fcntl.flock` (via closing the underlying file fd). After `close()`, the `Database` MUST NOT be usable.

#### Scenario: Close releases file lock
- **WHEN** `Database.close()` is called
- **THEN** the underlying Pager file fd is closed
- **AND** the OS automatically releases the flock held on that fd
- **AND** another process waiting on the lock can now acquire it