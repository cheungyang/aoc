import os

def agent_prompt_hook(current_prompt, **kwargs):
    agent_path = kwargs.get("agent_path")
    if not agent_path:
        return current_prompt

    prompt_parts = []
    for file_name in os.listdir(agent_path):
        if file_name.endswith(".md"):
            with open(os.path.join(agent_path, file_name), "r") as f:
                content = f.read()
            prompt_parts.append(f"## {file_name}\n{content}")

    md_prompt = "\n\n".join(prompt_parts)
    
    if current_prompt:
        return md_prompt + "\n\n" + current_prompt
    return md_prompt

def register_hooks():
    return {
        "system_prompt": agent_prompt_hook
    }
