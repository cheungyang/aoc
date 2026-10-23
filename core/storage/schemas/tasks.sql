-- The task cache (core/storage/tasks.py): one row per task line in the vault and
-- the TickTick export, rebuilt from the markdown by core/knowledge/tasks/sync.py.
-- Dates are YYYY-MM-DD text; `tags` is a JSON array of tags without their '#'.
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    raw_title TEXT,
    status TEXT NOT NULL CHECK(status IN ('todo', 'completed', 'dropped')),
    priority TEXT,
    priority_rank INTEGER DEFAULT 99,
    tags TEXT,
    scheduled_date TEXT,
    due_date TEXT,
    completed_date TEXT,
    dropped_date TEXT,
    created_date TEXT,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('ticktick', 'vault')),
    line_number INTEGER NOT NULL,
    raw_line TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_date TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority_rank ON tasks(priority_rank);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled_date ON tasks(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_tasks_source ON tasks(source);
CREATE INDEX IF NOT EXISTS idx_tasks_updated_date ON tasks(updated_date);
