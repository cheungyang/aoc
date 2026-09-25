#!/usr/bin/env python3
"""
Compacts and optimizes sessions/memory.db:
1. Recompresses all existing checkpoint and write blobs with zlib.
2. Recursively strips/downscales redundant oversized inline image base64 strings in all checkpoints, writes, and task payloads.
   Oversized ToolMessage text is shrunk to the runtime cap (HA error_log sections condensed first).
3. Prunes old intermediate step checkpoints (keeping the latest 10 per context).
4. Executes VACUUM to reclaim disk space.
"""
import os
import sys
import sqlite3
import pickle
import zlib
import base64

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.util import compress_image_bytes
from core.util.config import Config
from core.util.message_util import cap_tool_output
from core.integrations.homeassistant import error_log as ha_error_log
from langchain_core.messages import ToolMessage

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sessions", "memory.db"))

_ERROR_LOG_OPEN = '<instruction_result action="error_log">'
_RESULT_CLOSE = '</instruction_result>'


def shrink_tool_text(text: str, max_chars: int) -> str:
    """Applies today's runtime limits to a tool result stored before they existed.

    HA error_log sections are condensed first (keeps every distinct problem),
    then the whole result is capped as `cap_tool_output` would have.
    """
    if not isinstance(text, str) or len(text) <= max_chars:
        return text
    start = text.find(_ERROR_LOG_OPEN)
    while start != -1:
        body_start = start + len(_ERROR_LOG_OPEN)
        end = text.find(_RESULT_CLOSE, body_start)
        if end == -1:
            break
        body = ha_error_log.condense(text[body_start:end])
        text = text[:body_start] + body + text[end:]
        start = text.find(_ERROR_LOG_OPEN, body_start + len(body))
    return cap_tool_output(text, max_chars)


def clean_element(obj):
    if isinstance(obj, ToolMessage):
        if isinstance(obj.content, str):
            new = shrink_tool_text(obj.content, Config().tool_output_max_chars or 200000)
            if new is not obj.content and new != obj.content:
                print(f"  Shrunk ToolMessage '{obj.name}' {len(obj.content)} -> {len(new)} chars")
                obj.content = new
        return
    if isinstance(obj, dict):
        if obj.get("type") == "image_url" and isinstance(obj.get("image_url"), dict):
            url = obj["image_url"].get("url", "")
            if url.startswith("data:image/") and ";base64," in url:
                header, b64data = url.split(";base64,", 1)
                if len(b64data) > 200000:
                    try:
                        raw_bytes = base64.b64decode(b64data)
                        comp_bytes, mime = compress_image_bytes(raw_bytes, max_dim=1560, quality=75)
                        new_b64 = base64.b64encode(comp_bytes).decode("utf-8")
                        obj["image_url"]["url"] = f"data:{mime};base64,{new_b64}"
                    except Exception:
                        pass
        for v in obj.values():
            clean_element(v)
    elif isinstance(obj, (list, tuple, set)) or hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        for item in obj:
            clean_element(item)
    elif hasattr(obj, "content"):
        clean_element(obj.content)
    
    # Process special LangGraph / LangChain structures
    for attr in ("arg", "args", "channel_values", "messages", "writes", "checkpoint", "state"):
        if hasattr(obj, attr):
            try:
                val = getattr(obj, attr)
                clean_element(val)
            except Exception:
                pass

def compact_db(db_path=DB_PATH, thread_id=None):
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    initial_size = os.path.getsize(db_path) / (1024 * 1024)
    print(f"Initial DB Size: {initial_size:.2f} MB")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")  # coexist with a running bot
    cursor = conn.cursor()

    if thread_id:
        from core.knowledge.memory.sqlite_checkpointer import sanitize_table_name
        tables = [sanitize_table_name(thread_id)]
    else:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%'")
        tables = [r["name"] for r in cursor.fetchall()]

    for tname in tables:
        # 1. Prune intermediate checkpoints, keep last 10
        cursor.execute(f"""
            DELETE FROM "{tname}"
            WHERE entry_type = 'checkpoint'
            AND id NOT IN (
                SELECT id FROM "{tname}"
                WHERE entry_type = 'checkpoint'
                ORDER BY step DESC, id DESC
                LIMIT 10
            )
        """)
        
        # 2. Prune orphan writes
        cursor.execute(f"""
            DELETE FROM "{tname}"
            WHERE entry_type = 'write'
            AND checkpoint_id NOT IN (
                SELECT checkpoint_id FROM "{tname}"
                WHERE entry_type = 'checkpoint'
            )
        """)
        conn.commit()

        # 3. Compress remaining blob rows
        cursor.execute(f'SELECT id, entry_type, data FROM "{tname}" WHERE data IS NOT NULL')
        rows = cursor.fetchall()
        for r in rows:
            row_id = r["id"]
            blob = r["data"]
            try:
                try:
                    obj = pickle.loads(zlib.decompress(blob))
                except Exception:
                    obj = pickle.loads(blob)
                
                clean_element(obj)
                
                # Re-serialize with zlib compression
                compressed_blob = zlib.compress(pickle.dumps(obj), level=6)
                cursor.execute(f'UPDATE "{tname}" SET data = ? WHERE id = ?', (compressed_blob, row_id))
            except Exception as e:
                print(f"  Warning: could not process row {row_id} in {tname}: {e}")
        conn.commit()

    conn.close()

    # 4. VACUUM database to reclaim disk pages
    print("Running VACUUM to reclaim free pages...")
    conn = sqlite3.connect(db_path)
    conn.execute("VACUUM")
    conn.close()

    final_size = os.path.getsize(db_path) / (1024 * 1024)
    reduction = ((initial_size - final_size) / initial_size) * 100 if initial_size > 0 else 0
    print(f"Final DB Size: {final_size:.2f} MB ({reduction:.1f}% reduction)")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--thread", help="Only compact this session/thread id (e.g. 'home-steward:tool:home-automation').")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()
    compact_db(args.db, thread_id=args.thread)
