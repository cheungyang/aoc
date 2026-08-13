from abc import ABC
import os
import asyncio
import discord
from typing import List
import subprocess
from core.agent.base_agent import BaseAgent
from core.agent.job_manager import JobManager, current_job_id, current_channel_name, current_agent_id
from core.agent.session_manager import SessionManager
from core.util import split_message

class ScriptExecutorAgent(BaseAgent):
    def __init__(self, agent_id: str, config: dict = None):
        super().__init__(agent_id, config or {})

    async def execute(self, content: str, source: str, job_id: str = None, channel: discord.TextChannel = None, callbacks: List = None, role: str = "user") -> str:
        session_id = SessionManager().get_session_id(self.agent_id, source, channel)
        if job_id is None:
            job_id = JobManager().new_job_id(self.agent_id)
            
        JobManager().add_job(job_id, self.agent_id, session_id, initial_prompt=content)
        JobManager().update_job(job_id, "running")

        channel_name = channel.name if channel and hasattr(channel, "name") else ""
        token = current_job_id.set(job_id)
        channel_token = current_channel_name.set(channel_name)
        agent_token = current_agent_id.set(self.agent_id)

        lines = content.strip().split('\n')
        results = []
        
        try:
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split(maxsplit=1)
                if not parts:
                    continue
                    
                action = parts[0]
                rest = parts[1] if len(parts) > 1 else ""
                
                if action == "tool":
                    if not rest:
                        results.append("Error: tool action requires a tool name and arguments.")
                        continue
                    
                    tool_parts = rest.split(maxsplit=1)
                    tool_name = tool_parts[0]
                    tool_args_str = tool_parts[1] if len(tool_parts) > 1 else "{}"
                    
                    try:
                        import json
                        tool_args = json.loads(tool_args_str)
                    except json.JSONDecodeError as e:
                        results.append(f"Error parsing JSON arguments for tool {tool_name}: {str(e)}")
                        continue
                        
                    from core.loaders.tools_loader import ToolsLoader
                    loader = ToolsLoader()
                    discovered = loader._discover_tools()
                    
                    if tool_name not in discovered:
                        results.append(f"Error: Tool {tool_name} not found.")
                        continue
                        
                    folder = discovered[tool_name]
                    if folder:
                        module_path = f"tools.{folder}.{tool_name}"
                    else:
                        module_path = f"tools.{tool_name}"
                        
                    try:
                        import importlib
                        mod = importlib.import_module(module_path)
                        if hasattr(mod, tool_name):
                            tool_obj = getattr(mod, tool_name)
                            
                            if hasattr(tool_obj, "ainvoke"):
                                res = await tool_obj.ainvoke(tool_args)
                            else:
                                import inspect
                                if inspect.iscoroutinefunction(tool_obj):
                                    res = await tool_obj(**tool_args)
                                else:
                                    res = tool_obj(**tool_args)
                                    
                            results.append(f"Tool {tool_name} executed successfully:\n{res}")
                        else:
                            results.append(f"Error: Module {module_path} does not have attribute {tool_name}")
                    except Exception as e:
                        results.append(f"Error executing tool {tool_name}: {str(e)}")
                        
                elif action == "script":
                    if not rest:
                        results.append("Error: script action requires a script name.")
                        continue
                    try:
                        import shlex
                        args = shlex.split(rest)
                        if args:
                            args[0] = os.path.join("scripts", args[0])
                        expanded_args = [os.path.expanduser(arg) for arg in args]
                        res = await asyncio.to_thread(subprocess.run, expanded_args, capture_output=True, text=True, check=True)
                        results.append(f"Script '{rest}' executed successfully:\n{res.stdout}")
                    except subprocess.CalledProcessError as e:
                        results.append(f"Error executing script '{rest}': {e.stderr}")
                    except Exception as e:
                        results.append(f"Error running script '{rest}': {str(e)}")
                else:
                    results.append(f"Unknown action: {action}")
                    
            JobManager().update_job(job_id, "completed")
        except Exception as e:
            JobManager().update_job(job_id, "error")
            results.append(f"Unexpected error during execution: {str(e)}")
        finally:
            current_job_id.reset(token)
            current_channel_name.reset(channel_token)
            current_agent_id.reset(agent_token)

        final_output = "\n".join(results)
        
        if channel is not None:
            chunks = split_message(final_output)
            for chunk in chunks:
                await channel.send(chunk)
                
        return final_output
