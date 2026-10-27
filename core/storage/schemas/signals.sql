-- The proactive Board (core/storage/signals.py): what the taps saw after each
-- agent turn, what the Switchboard made of it, what the Governor allowed, and
-- what you said about it. core/proactive/README.md walks through the flow.

-- One row per finished agent turn the taps keep (core/proactive/taps.py).
-- `decision_id` stays NULL until a Switchboard run reads the row.
CREATE TABLE IF NOT EXISTS turn_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,                 -- when the turn ended, epoch seconds
    agent_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',  -- ExecutionContext.source
    surface TEXT NOT NULL DEFAULT '', -- text, voice, scheduled, tool
    caller TEXT NOT NULL DEFAULT '',  -- the <caller> tag: "main" for a message routed from a channel
    tap TEXT NOT NULL,                -- effect, scheduled or chat: how soon the Switchboard runs
    user_text TEXT NOT NULL DEFAULT '',
    reply_text TEXT NOT NULL DEFAULT '',
    memory TEXT NOT NULL DEFAULT '[]',   -- JSON list: the reply's memory-log lines
    effects TEXT NOT NULL DEFAULT '[]',  -- JSON list: domain tool calls, {tool, actions, args, role, status}
    context TEXT NOT NULL DEFAULT '{}',  -- JSON: {"touched": [...], "skills": [...]}
    hops INTEGER NOT NULL DEFAULT 0,     -- 1 for a turn a proactive delivery started
    decision_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turn_ledger_pending ON turn_ledger(decision_id, session_id);
CREATE INDEX IF NOT EXISTS idx_turn_ledger_at ON turn_ledger(at);

-- One row per Switchboard run.
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,                 -- when the run started
    tap TEXT NOT NULL,                -- the most urgent tap among the turns it read
    session_id TEXT NOT NULL DEFAULT '',
    first_turn_at REAL,               -- the earliest turn it read: each tap's delay starts there
    mode TEXT NOT NULL,               -- shadow or live
    took_ms INTEGER,                  -- the Switchboard's own run
    tokens_in INTEGER,
    tokens_out INTEGER,
    reply TEXT NOT NULL DEFAULT '',   -- the Switchboard's reply, as it came
    prompt TEXT NOT NULL DEFAULT '',  -- what it was asked: G3's with-vs-without-memory replay re-runs it
    errors TEXT NOT NULL DEFAULT ''   -- why the run or its reply failed, if it did
);
CREATE INDEX IF NOT EXISTS idx_decisions_at ON decisions(at);

-- One row per signal a Switchboard run emitted, with code's evidence check.
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,
    at REAL NOT NULL,
    kind TEXT NOT NULL,               -- situation, artifact, request, event, focus
    gist TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    evidence_found INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '',    -- comma-separated, from TAGS.md
    until TEXT,                       -- YYYY-MM-DD, when the fact stops being true
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    payload TEXT NOT NULL DEFAULT '{}',
    source_agent TEXT NOT NULL DEFAULT '',
    source_channel TEXT NOT NULL DEFAULT '',
    hops INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_signals_decision ON signals(decision_id);

-- One row per route a signal named, with the Governor's verdict, when it would
-- be (or was) delivered, and your label.
CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    decision_id INTEGER NOT NULL,
    to_agent TEXT NOT NULL,
    offer TEXT NOT NULL DEFAULT '',
    asked TEXT NOT NULL,              -- the urgency the Switchboard asked for: now, today, next, absorb
    urgency TEXT NOT NULL,            -- the urgency the Governor granted
    window_start TEXT,
    window_end TEXT,
    note TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL,            -- deliver, absorb or drop
    reason TEXT NOT NULL DEFAULT '',  -- the Governor's reason for a drop, an absorb or a downgrade
    kind_key TEXT NOT NULL DEFAULT '',-- recipient/kind/tag: what acceptance is counted per
    sampled REAL,                     -- the acceptance draw behind "worth posting?"
    deliver_at REAL,                  -- when it would be delivered (shadow) or is due (live)
    delivered_at REAL,                -- when its proactive turn posted (live; the deliveries table)
    outcome TEXT,                     -- engaged, ignored, declined, missed
    radio_message_id TEXT,            -- its team radio line
    poll_message_id TEXT,             -- the reason poll under that line
    label TEXT,                       -- good or bad: your ✅/❌
    label_reason TEXT,                -- wrong agent, wrong timing, not worth it, wrong fact
    label_note TEXT,
    labeled_at REAL
);
CREATE INDEX IF NOT EXISTS idx_routes_decision ON routes(decision_id);
CREATE INDEX IF NOT EXISTS idx_routes_radio ON routes(radio_message_id);
CREATE INDEX IF NOT EXISTS idx_routes_poll ON routes(poll_message_id);
CREATE INDEX IF NOT EXISTS idx_routes_to ON routes(to_agent, verdict);

-- One row per route approved for delivery: live mode only (PROACTIVE_DELIVERY).
-- The delivery is a proactive turn of the recipient in its home channel
-- (core/proactive/delivery.py). A row goes approved → running → posted, silent
-- or failed, and never back. Each schedule runner tick claims a due row with one
-- conditional UPDATE, so it runs once. A row left running is failed, not
-- retried: it may have posted, and for posts to you at most once beats at least once.
CREATE TABLE IF NOT EXISTS deliveries (
    route_id INTEGER PRIMARY KEY,     -- routes.id: a route is delivered once
    to_agent TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '', -- the recipient's home channel: where its turn runs and posts
    deliver_at REAL NOT NULL,         -- when it's due, epoch seconds
    status TEXT NOT NULL,             -- approved, running, posted, silent or failed
    approved_at REAL,
    claimed_at REAL,                  -- when a tick took it
    finished_at REAL,
    error TEXT NOT NULL DEFAULT ''    -- why it failed
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries(status, deliver_at);

-- How each kind of nudge has gone: what "worth posting?" samples from.
CREATE TABLE IF NOT EXISTS acceptance (
    kind_key TEXT PRIMARY KEY,
    engaged INTEGER NOT NULL DEFAULT 0,
    ignored INTEGER NOT NULL DEFAULT 0,
    declined INTEGER NOT NULL DEFAULT 0,
    updated REAL
);

-- What you said should have reached an agent and didn't: `!signals missed ...`.
CREATE TABLE IF NOT EXISTS missed_routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    text TEXT NOT NULL,
    to_agent TEXT NOT NULL DEFAULT ''
);
