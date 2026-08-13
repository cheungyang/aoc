import unittest
import os
import shutil
import tempfile
import sys
import sqlite3
import pickle
import base64
import io
from PIL import Image

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scripts.compact_memory_db import compact_db, clean_element

class TestCompactMemoryDb(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "memory.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_clean_element_downscales_image(self):
        # Create a large dummy noisy image (PNG of 2000x2000)
        img = Image.effect_noise((2000, 2000), 50).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        
        # Test clean_element
        payload = {
            "checkpoint": {
                "channel_values": {
                    "messages": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{raw_b64}"
                            }
                        }
                    ]
                }
            }
        }
        
        clean_element(payload)
        new_url = payload["checkpoint"]["channel_values"]["messages"][0]["image_url"]["url"]
        new_b64 = new_url.split(";base64,")[1]
        self.assertTrue(len(new_b64) < len(raw_b64))

    def test_compact_db_executes(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE ctx_test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT NOT NULL,
                checkpoint_id TEXT,
                step INTEGER,
                data BLOB
            )
        """)
        # Insert 15 dummy checkpoints
        for i in range(15):
            conn.execute(
                "INSERT INTO ctx_test (entry_type, checkpoint_id, step, data) VALUES (?, ?, ?, ?)",
                ("checkpoint", f"cp_{i}", i, pickle.dumps({"val": i}))
            )
        # Insert matching write and orphan write
        conn.execute("INSERT INTO ctx_test (entry_type, checkpoint_id, step, data) VALUES (?, ?, ?, ?)", ("write", "cp_14", -1, pickle.dumps({"write": 14})))
        conn.execute("INSERT INTO ctx_test (entry_type, checkpoint_id, step, data) VALUES (?, ?, ?, ?)", ("write", "cp_0", -1, pickle.dumps({"write": 0})))
        conn.commit()
        conn.close()

        compact_db(self.db_path)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM ctx_test WHERE entry_type='checkpoint'")
        self.assertEqual(cursor.fetchone()[0], 10)
        
        cursor.execute("SELECT count(*) FROM ctx_test WHERE entry_type='write'")
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()

if __name__ == "__main__":
    unittest.main()
