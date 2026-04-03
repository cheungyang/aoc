import unittest
import os
import shutil
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import main

class TestHooksFramework(unittest.TestCase):

    def setUp(self):
        # Clear main hooks lists before testing
        main.pre_message_hooks.clear()
        main.post_message_hooks.clear()
        
        # Create real temporaries temporary dummy skill
        self.dummy_dir = "skills/dummy_test_hook"
        self.hooks_dir = os.path.join(self.dummy_dir, "hooks")
        os.makedirs(self.hooks_dir, exist_ok=True)
        
        # touch __init__.py markers package pack
        with open(os.path.join(self.hooks_dir, "__init__.py"), "w") as f:
             f.write("")
             
        # Write startup hook with identifiable markers attributes attributes
        with open(os.path.join(self.hooks_dir, "startup.py"), "w") as f:
             f.write("""
def dummy_pre(m, b): pass
dummy_pre.marker_id = "test_dummy_pre"

def dummy_post(m, b, t): pass
dummy_post.marker_id = "test_dummy_post"

def register_hooks():
    return {
        "pre_message": dummy_pre,
        "post_message": dummy_post
    }
""")

    def tearDown(self):
        # Cleanup temporaries temporary dummies
        if os.path.exists(self.dummy_dir):
             shutil.rmtree(self.dummy_dir)

    def test_load_hooks_detects_real_plugin_mounts(self):
        # Invoke scanner
        main.load_hooks()

        # Assertions without executing execution executions
        self.assertTrue(len(main.pre_message_hooks) >= 1)
        
        # Verify our marker exists in lists
        pre_found = any(getattr(hook, "marker_id", "") == "test_dummy_pre" for hook in main.pre_message_hooks)
        self.assertTrue(pre_found, "Pre-hook marker identifier not found in mounts lists")
        
        post_found = any(getattr(hook, "marker_id", "") == "test_dummy_post" for hook in main.post_message_hooks)
        self.assertTrue(post_found, "Post-hook marker identifier not found in mounts lists")

if __name__ == "__main__":
    unittest.main()
