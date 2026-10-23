-- The project cache (core/storage/projects.py): one row per project note in
-- vault/projects, rebuilt from the markdown by core/knowledge/projects/sync.py,
-- and one `project_commitments` row per year a project is committed to.
-- `commitments`, `aliases` and `tags` are JSON arrays.
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT,
    raw_status TEXT,
    commitment_year INTEGER,
    priority TEXT,
    priority_rank INTEGER DEFAULT 99,
    commitments TEXT,
    start_date TEXT,
    last_reviewed TEXT,
    last_updated TEXT,
    category TEXT,
    type TEXT DEFAULT 'project',
    aliases TEXT,
    tags TEXT,
    content_hash TEXT NOT NULL,
    updated_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_commitments (
    id TEXT NOT NULL,
    year INTEGER NOT NULL,
    priority TEXT,
    priority_rank INTEGER DEFAULT 99,
    PRIMARY KEY(id, year),
    FOREIGN KEY(id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_commitment_year ON projects(commitment_year);
CREATE INDEX IF NOT EXISTS idx_projects_priority_rank ON projects(priority_rank);
CREATE INDEX IF NOT EXISTS idx_projects_category ON projects(category);
CREATE INDEX IF NOT EXISTS idx_projects_last_reviewed ON projects(last_reviewed);
CREATE INDEX IF NOT EXISTS idx_projects_last_updated ON projects(last_updated);
CREATE INDEX IF NOT EXISTS idx_projects_file_path ON projects(file_path);

CREATE INDEX IF NOT EXISTS idx_commitments_id ON project_commitments(id);
CREATE INDEX IF NOT EXISTS idx_commitments_year ON project_commitments(year);
CREATE INDEX IF NOT EXISTS idx_commitments_priority_rank ON project_commitments(priority_rank);
