import unittest
import os
import shutil
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.hook_loader import HookLoader

class TestHooksFramework(unittest.TestCase):

    def setUp(self):
        self.loader = HookLoader()
        
        # Create temporary dummy hook in core
        self.dummy_dir = "core/dummy_test_hook"
        os.makedirs(self.dummy_dir, exist_ok=True)
        self.hooks_file = os.path.join(self.dummy_dir, "hooks.py")
        
        with open(self.hooks_file, "w") as f:
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
        self.loader.load_hooks()

        # Assertions without executing execution executions
        self.assertTrue(len(self.loader.pre_message_hooks) >= 1)
        
        # Verify our marker exists in lists
        pre_found = any(getattr(hook, "marker_id", "") == "test_dummy_pre" for hook in self.loader.pre_message_hooks)
        self.assertTrue(pre_found, "Pre-hook marker identifier not found in mounts lists")
        
        post_found = any(getattr(hook, "marker_id", "") == "test_dummy_post" for hook in self.loader.post_message_hooks)
        self.assertTrue(post_found, "Post-hook marker identifier not found in mounts lists")

if __name__ == "__main__":
    unittest.main()
