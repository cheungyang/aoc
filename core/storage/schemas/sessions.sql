-- One session's table (core/storage/sessions.py): "{{table}}" is ctx_ plus the
-- sanitized session or LangGraph thread id. Two writers share it, one table per
-- session/thread: the checkpointer (core/knowledge/memory/sqlite_checkpointer.py)
-- saves LangGraph's state as `checkpoint` and `write` rows, and the session
-- store saves the transcript, token use and voice turn traces as `message`,
-- `token` and `turn_trace` rows. A main-agent session's thread id is its session
-- id, so both write the same table. Tables made by older code keep their column
-- order; SessionStore adds the columns they lack.
CREATE TABLE IF NOT EXISTS "{{table}}" (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type TEXT NOT NULL,           -- checkpoint, write, message, token or turn_trace
    checkpoint_id TEXT,                 -- checkpoint and write rows; not unique
    step INTEGER DEFAULT -1,            -- checkpoint rows: the LangGraph step
    data BLOB,                          -- checkpoint and write rows: a zlib-compressed pickle
    metadata TEXT,                      -- checkpoint rows: JSON, never read back
    config TEXT,                        -- checkpoint rows: JSON, never read back
    parent_config TEXT,                 -- never written
    from_role TEXT,                     -- message rows: user, ai or system
    message TEXT,                       -- message rows; turn_trace rows: the trace as JSON
    model TEXT,                         -- token rows, like the three below
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cached_tokens REAL DEFAULT 0.0,     -- the share of input tokens read from the cache, in %
    execution_time REAL DEFAULT 0.0,    -- seconds: the model call (token), to first audio (turn_trace; NULL if none)
    surface TEXT,                       -- token and turn_trace rows: voice, text, scheduled...
    created_at REAL NOT NULL            -- epoch seconds
);
-- Both writers' indexes. Archiving drops an archived table's indexes, so the
-- session's next table can take their names.
CREATE INDEX IF NOT EXISTS "idx_{{table}}_entry_step" ON "{{table}}" (entry_type, step, id);
CREATE INDEX IF NOT EXISTS "idx_{{table}}_cpid" ON "{{table}}" (entry_type, checkpoint_id);
CREATE INDEX IF NOT EXISTS "idx_{{table}}_type" ON "{{table}}" (entry_type);
CREATE INDEX IF NOT EXISTS "idx_{{table}}_cp_id" ON "{{table}}" (checkpoint_id);
CREATE INDEX IF NOT EXISTS "idx_{{table}}_created" ON "{{table}}" (created_at);
