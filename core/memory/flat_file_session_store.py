import os
import json
import time
import shutil

# Compute SESSIONS_DIR relative to this file's location
SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sessions"))

class FlatFileSessionStore:
    def __init__(self, sessions_dir=SESSIONS_DIR):
        self.sessions_dir = sessions_dir
        self.archive_dir = os.path.join(self.sessions_dir, "archive")

    def get_file_path(self, session_id):
        safe_id = session_id.replace(":", "_").replace("/", "_")
        return os.path.join(self.sessions_dir, f"{safe_id}.json")

    def append_message(self, session_id, from_user, message):
        os.makedirs(self.sessions_dir, exist_ok=True)
        file_path = self.get_file_path(session_id)
        
        log_entry = {"from": from_user, "message": message, "ts": int(time.time())}
        data = []
        if os.path.exists(file_path):
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                pass
                
        data.append(log_entry)
        
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)
            
        return f"Appended message to {session_id}"

    def archive_session(self, session_id):
        from core.memory.flat_file_checkpointer import FlatFileCheckpointer
        
        file_path = self.get_file_path(session_id)
        if os.path.exists(file_path):
            os.makedirs(self.archive_dir, exist_ok=True)
            safe_id = session_id.replace(":", "_").replace("/", "_")
            archive_name = f"{safe_id}_{int(time.time())}.json"
            archive_path = os.path.join(self.archive_dir, archive_name)
            shutil.move(file_path, archive_path)
            
            # Also delete checkpointer data
            saver = FlatFileCheckpointer()
            saver.delete_thread(session_id)
            
            return f"Session {session_id} archived to archive/{archive_name}"
        return "No active session file found to archive."

    def load_history(self, session_id, limit=50):
        file_path = self.get_file_path(session_id)
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
