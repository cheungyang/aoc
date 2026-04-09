import asyncio
import uuid
import datetime
import sys
import os

# Ensure we can import from core
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from .agents_loader import AgentsLoader


class SubagentManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SubagentManager, cls).__new__(cls)
            cls._instance.tasks = {} # job_id -> {status, result, question, task_obj}
        return cls._instance

    def launch_task(self, agent_id, prompt):
        date_str = datetime.date.today().isoformat()
        job_id = f"{agent_id}:{date_str}"
        self.tasks[job_id] = {
            "status": "running",
            "result": None,
            "question": None,
            "task_obj": None
        }
        
        # Start background task
        task = asyncio.create_task(self._run_subagent(job_id, agent_id, prompt))
        self.tasks[job_id]["task_obj"] = task
        return job_id

    async def _run_subagent(self, job_id, agent_id, prompt):
        try:
            loader = AgentsLoader()
            agent = loader.get_agent(agent_id)
            
            # Execute graph
            response = await agent.execute(prompt, job_id)
            
            self.tasks[job_id]["status"] = "success"
            self.tasks[job_id]["result"] = response
                
        except asyncio.CancelledError:
            self.tasks[job_id]["status"] = "cancelled"
        except Exception as e:
            self.tasks[job_id]["status"] = "error"
            self.tasks[job_id]["result"] = str(e)

    def check_task(self, job_id):
        if job_id not in self.tasks:
            return {"status": "not_found"}
        
        task_info = self.tasks[job_id]
        return {
            "status": task_info["status"],
            "result": task_info["result"],
            "question": task_info["question"]
        }

    def cancel_task(self, job_id):
        if job_id not in self.tasks:
            return "not_found"
        
        task_info = self.tasks[job_id]
        if task_info["status"] == "running" and task_info["task_obj"]:
            task_info["task_obj"].cancel()
            return "cancelling"
        return task_info["status"]

    def update_task(self, job_id, user_input):
        if job_id not in self.tasks:
            return "not_found"
            
        task_info = self.tasks[job_id]
        if task_info["status"] == "needs_input":
            # Placeholder for resuming with input
            # Requires deeper integration with LangGraph checkpointer
            return "resuming_not_implemented"
            
        return "not_applicable"
