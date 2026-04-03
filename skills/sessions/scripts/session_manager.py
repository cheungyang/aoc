#!/usr/bin/env python3
import sys
import os
import json
import time
import shutil

# Resolve relative to this script: skills/sessions/scripts/session_manager.py -> sessions/
SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "sessions"))
ARCHIVE_DIR = os.path.join(SESSIONS_DIR, "archive")

def get_file_path(session_id):
    # Safe filename: replace : with _ to be safe on all OS
    safe_id = session_id.replace(":", "_")
    return os.path.join(SESSIONS_DIR, f"{safe_id}.json")

def append_message(session_id, from_user, message):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    file_path = get_file_path(session_id)
    
    log_entry = {
        "ts": int(time.time()),
        "from": from_user,
        "message": message
    }
    
    data = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            pass # Handle corrupted file if necessary
            
    data.append(log_entry)
    
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)
        
    return f"Appended message to {session_id}"

def archive_session(session_id):
    file_path = get_file_path(session_id)
    if os.path.exists(file_path):
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        safe_id = session_id.replace(":", "_")
        archive_name = f"{safe_id}_{int(time.time())}.json"
        archive_path = os.path.join(ARCHIVE_DIR, archive_name)
        shutil.move(file_path, archive_path)
        return f"Session {session_id} archived to archive/{archive_name}"
    return "No active session file found to archive."

def load_history(session_id, limit=50):
    file_path = get_file_path(session_id)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                if limit and limit > 0:
                     return data[-limit:]
                return data
        except json.JSONDecodeError:
            return []
    return []

# CLI interface for Agent investigate adoption
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python session_manager.py <command> [args]")
        print("Commands: append <session_id> <from> <message> | load <session_id>")
        sys.exit(1)
        
    cmd = sys.argv[1]
    if cmd == "append" and len(sys.argv) >= 5:
        print(append_message(sys.argv[2], sys.argv[3], sys.argv[4]))
    elif cmd == "load" and len(sys.argv) >= 3:
        history = load_history(sys.argv[2])
        print(json.dumps(history, indent=2))
    else:
        print("Invalid args usage.")
        sys.exit(1)
