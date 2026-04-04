import os
import importlib

class HookLoader:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HookLoader, cls).__new__(cls)
            cls._instance.pre_message_hooks = []
            cls._instance.post_message_hooks = []
            cls._instance.system_prompt_hooks = []
            cls._instance._load_hooks()
        return cls._instance

    def __init__(self):
        pass

    def _load_hooks(self):
        # Scan core/ for *_hook.py files
        for item in os.listdir("core"):
            item_path = os.path.join("core", item)
            if os.path.isdir(item_path):
                for file_name in os.listdir(item_path):
                    if file_name.endswith("_hook.py"):
                        module_name = file_name[:-3]
                        module_path = f"core.{item}.{module_name}"
                        self._load_module_hooks(module_path)
            elif os.path.isfile(item_path) and item.endswith("_hook.py"):
                module_name = item[:-3]
                module_path = f"core.{module_name}"
                self._load_module_hooks(module_path)

    def _load_module_hooks(self, module_path):
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, "register_hooks"):
                hooks = module.register_hooks()
                if "pre_message" in hooks:
                    self.pre_message_hooks.append(hooks["pre_message"])
                if "post_message" in hooks:
                    self.post_message_hooks.append(hooks["post_message"])
                if "system_prompt" in hooks:
                    self.system_prompt_hooks.append(hooks["system_prompt"])
                print(f"Loaded hooks from {module_path}")
        except Exception as e:
            print(f"Failed to load hooks from {module_path}: {e}")
