-- Full-text search over the task cache (tasks.sql), kept in step with `tasks` by
-- its triggers. Optional: TasksStore skips it where SQLite has no FTS5, and the
-- virtual table comes first so a failure leaves no trigger behind.
CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    id UNINDEXED,
    title,
    tags,
    source,
    content='tasks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
  INSERT INTO tasks_fts(rowid, id, title, tags, source)
  VALUES (new.rowid, new.id, new.title, new.tags, new.source);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, id, title, tags, source)
  VALUES('delete', old.rowid, old.id, old.title, old.tags, old.source);
END;

CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
  INSERT INTO tasks_fts(tasks_fts, rowid, id, title, tags, source)
  VALUES('delete', old.rowid, old.id, old.title, old.tags, old.source);
  INSERT INTO tasks_fts(rowid, id, title, tags, source)
  VALUES (new.rowid, new.id, new.title, new.tags, new.source);
END;
