-- The learning loop's tables (core/storage/learning.py). Phase 0 only observes:
-- what the nightly detectors found, what the runtime logged for them, which
-- prompt each model call ran on, and how big each agent's prompt is.
-- core/learning/README.md walks through the flow.

-- One row per friction a detector found (core/learning/detectors.py). The
-- unique key makes a rerun over the same rows add nothing twice.
CREATE TABLE IF NOT EXISTS friction_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,                       -- when the friction happened, epoch seconds
    detector TEXT NOT NULL,                 -- correction, rephrase, tool_failure... (README: the detectors)
    source TEXT NOT NULL,                   -- the table it rests on: turn_ledger, routes, ctx_..., learning_events
    source_id TEXT NOT NULL,                -- the row or rows there: "412", "231-236", a message id
    agent_id TEXT NOT NULL DEFAULT '',      -- whose friction it is
    session TEXT NOT NULL DEFAULT '',       -- the session id or ctx_ table it happened in
    category TEXT NOT NULL DEFAULT '',      -- a hint from the closed list; '' when code can't tell
    evidence TEXT NOT NULL DEFAULT '',      -- what was seen: your words, an error's first line; clipped
    evidence_role TEXT NOT NULL DEFAULT '', -- whose words the evidence is: user, agent, tool or system
    severity INTEGER NOT NULL DEFAULT 1,    -- 1 cosmetic, 2 an extra turn or 30 s, 3 a failed task or a wrong action
    cost_turns INTEGER,                     -- extra turns it cost, when code can count them
    cost_seconds REAL,                      -- time it cost, when code can measure it
    details TEXT NOT NULL DEFAULT '{}',     -- JSON: what the detector measured
    label TEXT,                             -- your verdict: real or not (`!learn label`)
    labeled_at REAL,
    found_at REAL NOT NULL                  -- when the scan found it
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_friction_events_once ON friction_events(detector, source, source_id);
CREATE INDEX IF NOT EXISTS idx_friction_events_at ON friction_events(at);
CREATE INDEX IF NOT EXISTS idx_friction_events_agent ON friction_events(agent_id, at);

-- What the runtime tells the learning loop that no other table keeps
-- (core/learning/events.py): commands, refused actions, voice turns that came
-- without their <speech>, and your 👎 on an agent's message.
CREATE TABLE IF NOT EXISTS learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    kind TEXT NOT NULL,                     -- command, refusal, speech_fallback or reaction
    agent_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',          -- the command, the refused tool, the emoji
    detail TEXT NOT NULL DEFAULT '{}'       -- JSON: the command's arguments, the refused action and path...
);
CREATE INDEX IF NOT EXISTS idx_learning_events_kind ON learning_events(kind, at);

-- Which prompt a model call ran on (core/learning/versions.py): its version is
-- "<static>.<memory>", two short hashes, kept on the call's token row.
CREATE TABLE IF NOT EXISTS prompt_versions (
    version TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    first_seen REAL NOT NULL,
    static_chars INTEGER NOT NULL DEFAULT 0, -- the repo files it was built from: persona, agent.json, templates, skills
    memory_chars INTEGER NOT NULL DEFAULT 0, -- the vault files: USER.md, Profile, topics, MEMORY, FEEDBACK
    files TEXT NOT NULL DEFAULT '{}',       -- JSON: each file's path and the hash of its text
    PRIMARY KEY (agent_id, version)
);

-- Every repo prompt file's text, by hash, so a version can be shown, compared
-- or restored. Vault files aren't kept here: the vault's git keeps them.
CREATE TABLE IF NOT EXISTS prompt_files (
    sha TEXT PRIMARY KEY,                   -- sha256 of the text
    path TEXT NOT NULL,                     -- the repo path it was first seen at
    first_seen REAL NOT NULL,
    content TEXT NOT NULL
);

-- A daily snapshot of each agent's prompt size (core/learning/ledger.py).
CREATE TABLE IF NOT EXISTS prompt_ledger (
    day TEXT NOT NULL,                      -- YYYY-MM-DD, on your wall clock
    agent_id TEXT NOT NULL,
    persona_chars INTEGER NOT NULL DEFAULT 0,  -- AGENTS, INSTRUCTIONS, IDENTITY and SOUL, as the prompt loads them
    preload_chars INTEGER NOT NULL DEFAULT 0,  -- preloaded skill text
    user_chars INTEGER NOT NULL DEFAULT 0,     -- the agent's USER.md
    shared_chars INTEGER NOT NULL DEFAULT 0,   -- the Profile and its subscribed topics, as rendered
    memory_chars INTEGER NOT NULL DEFAULT 0,   -- its MEMORY, as rendered into the prompt
    feedback_chars INTEGER NOT NULL DEFAULT 0, -- its FEEDBACK, as rendered into the prompt
    instructions INTEGER NOT NULL DEFAULT 0,   -- instruction-like lines in persona and preloads (ledger.instruction_count)
    PRIMARY KEY (day, agent_id)
);

-- One row per nightly scan (scripts/learning_nightly.py): when, over what, and what came of it.
CREATE TABLE IF NOT EXISTS learning_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    since REAL NOT NULL,                    -- the window it read
    until REAL NOT NULL,
    found INTEGER NOT NULL DEFAULT 0,       -- friction events the detectors returned
    added INTEGER NOT NULL DEFAULT 0,       -- of those, new ones
    took_s REAL,
    errors TEXT NOT NULL DEFAULT ''         -- detectors that failed, and why
);
