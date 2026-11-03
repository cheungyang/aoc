-- Hand-off capsules (core/storage/handoff_capsules.py): the conversation an
-- agent's <switch to="..."> handed you over from, parked for the teammate.
-- graphs/main parks one when such a reply ends, and attaches it, once, to your
-- next message to that teammate in its home channel (a router hand-off to it
-- takes it too). A newer one for the same teammate and channel supersedes it;
-- one not taken by expires_at is never attached.
CREATE TABLE IF NOT EXISTS handoff_capsules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,                  -- when it was parked, epoch seconds
    from_agent TEXT NOT NULL,                  -- the agent whose reply handed you over
    from_channel TEXT NOT NULL,                -- the channel that reply was in (a thread's parent)
    to_agent TEXT NOT NULL,                    -- the teammate it's for
    to_channel TEXT NOT NULL,                  -- the teammate's home channel, where it's attached
    via TEXT NOT NULL DEFAULT 'switch',        -- how you were handed over: switch, an agent's <switch>
    brief TEXT NOT NULL DEFAULT '',            -- what the agent wrote inside the tag for the teammate; '' if nothing
    capsule TEXT NOT NULL,                     -- the <handoff_context> block, as it's attached
    expires_at REAL NOT NULL,                  -- created_at + the store's TTL: never attached after it
    consumed_at REAL,                          -- when a turn took it; NULL while it waits
    consumed_session TEXT NOT NULL DEFAULT '', -- the session it was attached in: the teammate's, in to_channel
    superseded_at REAL                         -- when a newer one for the same teammate and channel replaced it
);
CREATE INDEX IF NOT EXISTS idx_handoff_capsules_waiting ON handoff_capsules(to_agent, to_channel, consumed_at);
