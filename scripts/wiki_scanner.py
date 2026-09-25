#!/usr/bin/env python3
"""
wiki_scanner.py

A State & Semantic Radar script to be run as a crontab.
It identifies a list of topics for wiki-gardener agent to propose updates to the pkm/wiki/ space.
Outputs to: pkm/wiki/pending_lint.json, as a `review_queue` the wiki_lint skill reads.

Scope: only wiki folders marked with an `index.md` (see `wiki_scope`), and
duplicate pairs only between files in the same folder. A concept distilled from
a summary, or a cluster restating its concepts, is meant to overlap; comparing
like with like keeps the queue to real candidates.

Decisions stick: pairs the user chose to keep, and clear false positives
wiki-gardener dismissed itself, are listed under `dismissed` in
pkm/wiki/lint_state.json and not queued again until one of the files changes.
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Set

import numpy as np
from dateutil.relativedelta import relativedelta

# Ensure project root is importable when run directly from cron.
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.knowledge.vector.db import get_knowledge_db_path, init_knowledge_db, scan_by_category
from core.knowledge.vector.sync import WIKI_FOLDER_MARKER, wiki_scope
from core.util.config import Config

PENDING_FILE = os.path.join("wiki", "pending_lint.json")
STATE_FILE = os.path.join("wiki", "lint_state.json")

SIMILARITY_THRESHOLD = 0.92
JACCARD_THRESHOLD = 0.15
STALE_AFTER_MONTHS = 6

DUPLICATE = "duplicate_candidate"
STALE_STUB = "stale_stub"


def has_work(ctx):
    """Scheduler gate. Always yes: a stub becomes stale by ageing past six months,
    with no file change to detect, so "nothing changed" does not mean "nothing
    new to report". The scan is local and makes no model call."""
    return True, "stale-stub detection is time-based"


def compute_cosine_similarity(vectors):
    """Computes pairwise cosine similarity for a batch of vectors."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    normalized = vectors / norms
    return np.dot(normalized, normalized.T)


def in_scope_rows(rows: List[Dict], pkm_dir: str, in_scope: Set[str]) -> List[Dict]:
    """Rows from marked folders, minus the index files themselves.

    The index can still hold rows from folders that lost their marker until the
    next sync prunes them, so scope is checked here too.
    """
    kept = []
    for row in rows:
        rel = row.get("file_path") or ""
        if os.path.basename(rel) == WIKI_FOLDER_MARKER:
            continue
        if os.path.dirname(os.path.join(pkm_dir, rel)) in in_scope:
            kept.append(row)
    return kept


def _is_stub(tag_str) -> bool:
    if not tag_str:
        return False
    try:
        tag_list = json.loads(tag_str)
        return any("stub" in str(t).lower() for t in tag_list)
    except (json.JSONDecodeError, TypeError):
        return "stub" in str(tag_str).lower()


def _parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed


def find_stale_stubs(rows: List[Dict], now: datetime) -> Set[str]:
    cutoff = now - relativedelta(months=STALE_AFTER_MONTHS)
    stale = set()
    for row in rows:
        if not _is_stub(row.get("tags")):
            continue
        updated = _parse_time(row.get("updated_at"))
        if updated is not None and updated < cutoff:
            stale.add(row.get("file_path"))
    return stale


def find_duplicate_pairs(rows: List[Dict]) -> Set[tuple]:
    """File pairs in the same folder whose averaged embeddings are near-identical
    and whose vocabularies overlap."""
    by_folder: Dict[str, Dict[str, Dict]] = {}
    for row in rows:
        fp = row.get("file_path")
        doc = by_folder.setdefault(os.path.dirname(fp), {}).setdefault(fp, {"vectors": [], "text": ""})
        doc["vectors"].append(row.get("vector"))
        doc["text"] += " " + str(row.get("tags")) + " " + (row.get("text") or "").lower()

    pairs = set()
    for docs in by_folder.values():
        if len(docs) < 2:
            continue
        paths = list(docs)
        vectors = []
        words = []
        for fp in paths:
            avg = np.mean(docs[fp]["vectors"], axis=0)
            norm = np.linalg.norm(avg)
            vectors.append(avg / norm if norm > 0 else avg)
            words.append(set(re.findall(r"\w+", docs[fp]["text"])))
        sim = compute_cosine_similarity(np.array(vectors, dtype=np.float32))
        xs, ys = np.where(np.triu(sim, k=1) > SIMILARITY_THRESHOLD)
        for x, y in zip(xs, ys):
            w1, w2 = words[x], words[y]
            if not w1 or not w2:
                continue
            if len(w1 & w2) / len(w1 | w2) > JACCARD_THRESHOLD:
                pairs.add(tuple(sorted([paths[x], paths[y]])))
    return pairs


def fingerprint(pkm_dir: str, files: List[str]) -> Optional[str]:
    """Hash of the files' current contents; None if any is gone."""
    digest = hashlib.sha1()
    for rel in sorted(files):
        full = os.path.join(pkm_dir, rel)
        if not os.path.isfile(full):
            return None
        digest.update(rel.encode("utf-8"))
        with open(full, "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()


def load_json(path: str) -> Dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: could not read {path}: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def refresh_dismissed(pkm_dir: str, dismissed: List) -> List[Dict]:
    """Stamps new dismissals with a fingerprint and drops those whose files have
    changed or gone, so an edited pair is reviewed again."""
    kept = []
    for item in dismissed:
        if not isinstance(item, dict) or not isinstance(item.get("files"), list):
            continue
        current = fingerprint(pkm_dir, item["files"])
        if current is None:
            continue
        if "fingerprint" not in item:
            item = {**item, "fingerprint": current}
        elif item["fingerprint"] != current:
            continue
        kept.append(item)
    return kept


def _key(item_type: str, files) -> tuple:
    return (item_type, tuple(sorted(files)))


def build_queue(stale: Set[str], pairs: Set[tuple], dismissed: List[Dict]) -> List[Dict]:
    skip = {_key(d.get("type", DUPLICATE), d["files"]) for d in dismissed}
    queue = [{"type": DUPLICATE, "files": list(p)} for p in sorted(pairs)]
    queue += [{"type": STALE_STUB, "files": [fp]} for fp in sorted(stale)]
    return [item for item in queue if _key(item["type"], item["files"]) not in skip]


def write_json(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def run_scanner(now: Optional[datetime] = None):
    pkm_dir = Config().pkm_dir
    wiki_root = os.path.join(pkm_dir, "wiki")
    db_path = get_knowledge_db_path()

    print(f"Connecting to knowledge store at: {db_path}")
    try:
        handle = init_knowledge_db(db_path=db_path)
    except Exception as e:
        print(f"Failed to open the knowledge store: {e}")
        return

    in_scope, unindexed = wiki_scope(wiki_root)
    # Goes through the facade rather than a raw table scan because this script
    # needs the stored vectors, which search results omit.
    rows = in_scope_rows(scan_by_category(handle, "wiki"), pkm_dir, in_scope)
    print(f"Loaded {len(rows)} wiki chunks from {len(in_scope)} indexed folders.")

    stale = find_stale_stubs(rows, now or datetime.now())
    pairs = find_duplicate_pairs(rows)

    state_path = os.path.join(pkm_dir, STATE_FILE)
    state = load_json(state_path)
    dismissed = refresh_dismissed(pkm_dir, state.get("dismissed") or [])
    queue = build_queue(stale, pairs, dismissed)

    known = set(state.get("unindexed_folders") or [])
    for folder in unindexed:
        if folder not in known:
            print(f"New wiki folder without {WIKI_FOLDER_MARKER}: {folder}/ "
                  f"(not indexed or linted; add an {WIKI_FOLDER_MARKER} to include it)")

    new_state = {**state, "dismissed": dismissed, "unindexed_folders": sorted(unindexed)}
    if new_state != state:
        write_json(state_path, new_state)

    out_path = os.path.join(pkm_dir, PENDING_FILE)
    write_json(out_path, {"review_queue": queue})

    print(f"Scanner finished. Results saved to {out_path}")
    print(f"Queued {len(pairs)} duplicate pairs and {len(stale)} stale stubs, "
          f"minus {len(pairs) + len(stale) - len(queue)} dismissed: {len(queue)} to review.")


if __name__ == "__main__":
    run_scanner()
