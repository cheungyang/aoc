from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Dict, Any, Annotated, Sequence
from langchain_core.messages import BaseMessage, AIMessage
import operator
import json
import os
import asyncio
from core.loaders.agents_loader import AgentsLoader
from tools.git import git

class CodingState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    repo_path: str
    query: str
    tasks: List[Dict[str, Any]]
    active_tasks: List[int]
    completed_tasks: List[int]
    failed_tasks: List[int]
    attempts: Dict[str, int]  # task_id as string -> attempt count
    max_concurrency: int
    max_retries: int
    session_id: str
    error_message: str

git_lock = asyncio.Lock()

async def plan_node(state: CodingState):
    query = state.get("query")
    if not query:
        query = state["messages"][-1].content if state["messages"] else ""
        
    repo_path = state.get("repo_path")
    if not repo_path:
        repo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    session_id = state.get("session_id") or "default_session"
    session_file = os.path.join(repo_path, "sessions", f"breakdown_{session_id}.json")
    
    # Check if a session file already exists to support restartability
    if os.path.exists(session_file):
        try:
            with open(session_file, "r") as f:
                tasks = json.load(f)
            print(f"Subgraphs/Coding: Loaded existing task breakdown from {session_file}")
            return {
                "tasks": tasks,
                "repo_path": repo_path,
                "query": query,
                "attempts": {str(t["id"]): 0 for t in tasks},
                "completed_tasks": [],
                "failed_tasks": [],
                "active_tasks": []
            }
        except Exception as e:
            print(f"Subgraphs/Coding: Error reading session file: {e}")

    # Generate breakdown using software-planner
    planner = AgentsLoader().get_agent("software-planner")
    prompt = (
        f"We need to implement the following request: '{query}' in the repository at '{repo_path}'.\n"
        f"Deconstruct this request into a sequence of atomic coding tasks.\n"
        f"Each task must specify the description, target file_path, and clear acceptance criteria.\n"
        f"Output the result strictly as a valid JSON list matching this format:\n"
        f'[\n'
        f'  {{"id": 1, "description": "...", "file_path": "...", "acceptance_criteria": "..."}}\n'
        f']\n'
        f"Do not include any explanation, markdown blocks, or other text outside the JSON list."
    )
    
    response = await planner.execute(prompt, source="subgraph")
    
    cleaned_response = response.strip()
    # Strip markdown code block wrappers if present
    if cleaned_response.startswith("```"):
        lines = cleaned_response.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned_response = "\n".join(lines).strip()
        
    try:
        tasks = json.loads(cleaned_response)
        if not isinstance(tasks, list):
            raise ValueError("Parsed JSON is not a list")
    except Exception as e:
        print(f"Subgraphs/Coding: Error parsing planner breakdown: {e}. Raw response: {response}")
        # Fallback to single task using the original query
        tasks = [{
            "id": 1,
            "description": query,
            "file_path": "",
            "acceptance_criteria": "Implement the requested changes and pass tests."
        }]

    # Save to session breakdown file
    os.makedirs(os.path.dirname(session_file), exist_ok=True)
    try:
        with open(session_file, "w") as f:
            json.dump(tasks, f, indent=2)
    except Exception as e:
        print(f"Subgraphs/Coding: Failed to save breakdown file: {e}")

    return {
        "tasks": tasks,
        "repo_path": repo_path,
        "query": query,
        "attempts": {str(t["id"]): 0 for t in tasks},
        "completed_tasks": [],
        "failed_tasks": [],
        "active_tasks": []
    }

async def execute_tasks_node(state: CodingState):
    tasks = state["tasks"]
    completed = set(state.get("completed_tasks", []))
    failed = set(state.get("failed_tasks", []))
    attempts = dict(state.get("attempts", {}))
    max_concurrency = state.get("max_concurrency", 1)
    max_retries = state.get("max_retries", 2)
    repo_path = state["repo_path"]

    pending_tasks = [t for t in tasks if t["id"] not in completed and t["id"] not in failed]
    if not pending_tasks:
        return {}

    # Run up to max_concurrency tasks in parallel
    tasks_to_run = pending_tasks[:max_concurrency]

    async def run_single_task(task):
        task_id = task["id"]
        coder = AgentsLoader().get_agent("software-coder")
        qa = AgentsLoader().get_agent("software-qa")
        
        while attempts.get(str(task_id), 0) < max_retries:
            attempts[str(task_id)] = attempts.get(str(task_id), 0) + 1
            curr_attempt = attempts[str(task_id)]
            print(f"Subgraphs/Coding: Task {task_id} - Attempt {curr_attempt} of {max_retries}")
            
            # Call software-coder
            coder_prompt = (
                f"You are assigned task: '{task['description']}' in the repository at '{repo_path}'.\n"
                f"Target file path: '{task['file_path']}'.\n"
                f"Acceptance criteria: '{task['acceptance_criteria']}'.\n"
                f"Please implement the changes. Make sure they are correct."
            )
            coder_response = await coder.execute(coder_prompt, source="subgraph")
            print(f"Subgraphs/Coding: Task {task_id} - Coder finished execution")
            
            # Call software-qa
            qa_prompt = (
                f"Please verify the changes made for task: '{task['description']}'.\n"
                f"Repository: '{repo_path}'.\n"
                f"Target file path: '{task['file_path']}'.\n"
                f"Acceptance criteria: '{task['acceptance_criteria']}'.\n"
                f"Inspect the code, run tests, and return 'VERDICT: PASS' if the criteria are fully met, "
                f"or 'VERDICT: FAIL' with detailed error logs if not."
            )
            qa_response = await qa.execute(qa_prompt, source="subgraph")
            print(f"Subgraphs/Coding: Task {task_id} - QA verdict: {qa_response}")
            
            if "VERDICT: PASS" in qa_response:
                # Conductor: git commit
                async with git_lock:
                    commit_msg = f"feat: implement {task['description']}"
                    git.invoke({"command": "add .", "path": repo_path})
                    git.invoke({"command": f'commit -m "{commit_msg}"', "path": repo_path})
                return task_id, "completed", None
                
            print(f"Subgraphs/Coding: Task {task_id} - QA failed. Retrying...")

        return task_id, "failed", f"QA failed after {max_retries} attempts."

    results = await asyncio.gather(*(run_single_task(t) for t in tasks_to_run))

    new_completed = list(completed)
    new_failed = list(failed)
    error_message = state.get("error_message", "")

    for t_id, status, error in results:
        if status == "completed":
            new_completed.append(t_id)
        else:
            new_failed.append(t_id)
            if error:
                error_message += f"\nTask {t_id} failed: {error}"

    return {
        "completed_tasks": new_completed,
        "failed_tasks": new_failed,
        "attempts": attempts,
        "error_message": error_message.strip()
    }

def replan_node(state: CodingState):
    err = state.get("error_message") or "Unknown error"
    return {
        "messages": [AIMessage(content=f"Execution aborted due to task failure(s):\n{err}")]
    }

def finalize_node(state: CodingState):
    tasks_summary = "\n".join(f"- Task {t['id']}: {t['description']}" for t in state["tasks"])
    return {
        "messages": [AIMessage(content=f"Successfully implemented and verified all tasks:\n{tasks_summary}")]
    }

def should_continue(state: CodingState):
    tasks = state.get("tasks", [])
    completed = state.get("completed_tasks", [])
    failed = state.get("failed_tasks", [])

    if failed:
        return "replan"
    if len(completed) >= len(tasks):
        return "end"
    return "execute"

def create_graph(checkpointer=None, **kwargs):
    """Compiles and returns the coding orchestration graph."""
    workflow = StateGraph(CodingState)
    workflow.add_node("plan", plan_node)
    workflow.add_node("execute_tasks", execute_tasks_node)
    workflow.add_node("replan", replan_node)
    workflow.add_node("finalize", finalize_node)

    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "execute_tasks")

    workflow.add_conditional_edges(
        "execute_tasks",
        should_continue,
        {
            "execute": "execute_tasks",
            "replan": "replan",
            "end": "finalize"
        }
    )
    workflow.add_edge("replan", END)
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=checkpointer)

# Backward-compatible precompiled graph instance
graph = create_graph()

def prepare_input(query: str, caller: str = None, **kwargs) -> Dict[str, Any]:
    """Translates incoming text query into initial CodingState."""
    if caller and "<caller>" not in query:
        formatted_query = f"<caller>{caller}</caller>\n{query}"
    else:
        formatted_query = query

    return {
        "messages": [HumanMessage(content=formatted_query)],
        "query": formatted_query,
        "repo_path": kwargs.get("repo_path", ""),
        "session_id": kwargs.get("session_id", "default_session"),
        "max_concurrency": kwargs.get("max_concurrency", 1),
        "max_retries": kwargs.get("max_retries", 2)
    }

def format_output(state: Dict[str, Any]) -> str:
    """Formats final CodingState into response string."""
    if isinstance(state, dict):
        if "messages" in state and state["messages"]:
            last_msg = state["messages"][-1]
            if hasattr(last_msg, "content"):
                return last_msg.content
            elif isinstance(last_msg, dict) and "content" in last_msg:
                return last_msg["content"]
            return str(last_msg)
        if state.get("error_message"):
            return f"Coding failed: {state['error_message']}"
    return str(state)
