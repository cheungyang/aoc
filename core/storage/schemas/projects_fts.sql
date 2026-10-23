-- Full-text search over the project cache (projects.sql), kept in step with
-- `projects` by its triggers. Optional: ProjectsStore skips it where SQLite has
-- no FTS5, and the virtual table comes first so a failure leaves no trigger behind.
CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
    id UNINDEXED,
    name,
    tags,
    category,
    aliases,
    file_path,
    content='projects',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS projects_ai AFTER INSERT ON projects BEGIN
  INSERT INTO projects_fts(rowid, id, name, tags, category, aliases, file_path)
  VALUES (new.rowid, new.id, new.name, new.tags, new.category, new.aliases, new.file_path);
END;

CREATE TRIGGER IF NOT EXISTS projects_ad AFTER DELETE ON projects BEGIN
  INSERT INTO projects_fts(projects_fts, rowid, id, name, tags, category, aliases, file_path)
  VALUES('delete', old.rowid, old.id, old.name, old.tags, old.category, old.aliases, old.file_path);
END;

CREATE TRIGGER IF NOT EXISTS projects_au AFTER UPDATE ON projects BEGIN
  INSERT INTO projects_fts(projects_fts, rowid, id, name, tags, category, aliases, file_path)
  VALUES('delete', old.rowid, old.id, old.name, old.tags, old.category, old.aliases, old.file_path);
  INSERT INTO projects_fts(rowid, id, name, tags, category, aliases, file_path)
  VALUES (new.rowid, new.id, new.name, new.tags, new.category, new.aliases, new.file_path);
END;
