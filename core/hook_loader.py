import os
import importlib

class HookLoader:
    def __init__(self):
        self.pre_message_hooks = []
        self.post_message_hooks = []
        self.load_hooks()

    def load_hooks(self):
        # Scan core/* for hooks.py
        for folder in os.listdir("core"):
            startup_path = os.path.join("core", folder, "hooks.py")
            if os.path.exists(startup_path):
                 module_path = f"core.{folder}.hooks"
                 try:
                      module = importlib.import_module(module_path)
                      if hasattr(module, "register_hooks"):
                           hooks = module.register_hooks()
                           if "pre_message" in hooks:
                                self.pre_message_hooks.append(hooks["pre_message"])
                           if "post_message" in hooks:
                                self.post_message_hooks.append(hooks["post_message"])
                           print(f"Loaded hooks from {module_path}")
                 except Exception as e:
                      print(f"Failed to load hooks from {module_path}: {e}")
