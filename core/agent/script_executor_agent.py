from abc import ABC
import os
import discord
from typing import List
import subprocess
from core.agent.base_agent import BaseAgent
from core.agent.job_manager import JobManager
from core.agent.session_manager import SessionManager
from core.util import split_message

class ScriptExecutorAgent(BaseAgent):
    def __init__(self, agent_id: str, config: dict = None):
        super().__init__(agent_id, config or {})

    async def execute(self, content: str, source: str, job_id: str = None, channel: discord.TextChannel = None, callbacks: List = None, role: str = "user") -> str:
        session_id = SessionManager().get_session_id(self.agent_id, source, channel)
        if job_id is None:
            job_id = JobManager().new_job_id(self.agent_id)
            
        JobManager().add_job(job_id, self.agent_id, session_id)
        JobManager().updateJob(job_id, "running")

        lines = content.strip().split('\n')
        results = []
        
        try:
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split()
                if not parts:
                    continue
                    
                action = parts[0]
                args = parts[1:]
                
                if action == "script":
                    if not args:
                        results.append("Error: script action requires a path.")
                        continue
                    script_path = os.path.join("scripts", args[0])
                    import sys
                    cmd = [script_path]
                    if script_path.endswith('.py'):
                        cmd = [sys.executable, script_path]
                    elif script_path.endswith('.sh'):
                        cmd = ['bash', script_path]
                        
                    try:
                        res = subprocess.run(cmd + args[1:], capture_output=True, text=True, check=True)
                        results.append(f"Script {script_path} executed successfully:\n{res.stdout}")
                    except subprocess.CalledProcessError as e:
                        results.append(f"Error executing script {script_path}: {e.stderr}")
                    except Exception as e:
                        results.append(f"Error running script {script_path}: {str(e)}")
                        
                elif action == "exec":
                    if not args:
                        results.append("Error: exec action requires a command.")
                        continue
                    try:
                        expanded_args = [os.path.expanduser(arg) for arg in args]
                        res = subprocess.run(expanded_args, capture_output=True, text=True, check=True)
                        results.append(f"Command '{' '.join(args)}' executed successfully:\n{res.stdout}")
                    except subprocess.CalledProcessError as e:
                        results.append(f"Error executing command '{' '.join(args)}': {e.stderr}")
                    except Exception as e:
                        results.append(f"Error running command '{' '.join(args)}': {str(e)}")
                else:
                    results.append(f"Unknown action: {action}")
                    
            JobManager().updateJob(job_id, "completed")
        except Exception as e:
            JobManager().updateJob(job_id, "error")
            results.append(f"Unexpected error during execution: {str(e)}")

        final_output = "\n".join(results)
        
        if channel is not None:
            chunks = split_message(final_output)
            for chunk in chunks:
                await channel.send(chunk)
                
        return final_output
