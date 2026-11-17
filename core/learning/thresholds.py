"""Every number the friction detectors use, in one place (core/learning/detectors.py).

Each is a default to tune from your labels (`!learn label`): a detector whose
cards you keep marking "not" is too eager, one that misses what you see, too
strict. The README's detector table names each number next to its rule.
"""

# How far back the nightly scan reads by default. Re-reading days already seen
# costs little and adds nothing twice; it catches what a missed night left out.
LOOKBACK_S = 3 * 86400

# correction, rephrase: your next turn in the same session, this soon after the reply.
PAIR_GAP_S = 600
# rephrase: how alike your two turns read (difflib's ratio on normalised text),
# and the fewest words each needs, so "yes" and "ok" never look like a repeat.
REPHRASE_RATIO = 0.6
MIN_WORDS = 4

# agent_switch: the same request to another agent this soon after.
SWITCH_GAP_S = 1800

# abandoned_question: a reply that asked you something, unanswered this long,
# while you talked to the team elsewhere.
ABANDON_S = 24 * 3600

# post_edit: your edit of a file the agent wrote, this soon after, and at least this big.
POST_EDIT_S = 48 * 3600
POST_EDIT_MIN_LINES = 3
# post_edit: how long each git call may take before the detector gives up on it.
GIT_TIMEOUT_S = 10

# refusal_retry: a second refused try of the same tool by the same agent this soon after the first.
RETRY_GAP_S = 120

# round_trips: model calls in one turn above which it counts, per surface.
# Voice-hot flows target at most 2-3 calls (skills/agent_creation).
ROUND_TRIP_BUDGET = {"voice": 3, "text": 6}

# slow_turn: time to first audio above this multiple of the agent's p90 over the
# last SLOW_HISTORY_S, once there are SLOW_MIN_SAMPLES traces to measure against.
SLOW_FACTOR = 1.5
SLOW_HISTORY_S = 30 * 86400
SLOW_MIN_SAMPLES = 20

# unmet_follow_up: a follow-up you cancelled this soon after it opened.
QUICK_CANCEL_S = 24 * 3600

# A detector whose precision falls below MUTE_PRECISION, once you've labeled
# MUTE_AFTER of its cards, is muted until its rule is fixed.
MUTE_PRECISION = 0.5
MUTE_AFTER = 20

# How much of the evidence a card keeps.
EVIDENCE_CHARS = 300
