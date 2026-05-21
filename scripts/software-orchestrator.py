import subprocess
import json
import datetime
import ast
import re
import sys

MAX_JOB_DURATION_SECONDS = 7200
MAX_CONCURRENT_JOBS = 1

def run_command(cmd):
    print(f"Running command: {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running command: {cmd}")
        print(f"Stderr: {result.stderr.strip()}")
        return None
    return result.stdout

def run_gh_tool(command):
    # Escape single quotes in command for python string
    escaped_command = command.replace("'", "\\'")
    cmd = f".venv/bin/python3 -c \"from tools.gh import gh; print(gh.invoke({{'command': '{escaped_command}'}}))\""
    output = run_command(cmd)
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print(f"Could not find payload in gh tool response. Output: {output}")
        return None
    return match.group(1).strip()

def run_job_list_tool():
    cmd = ".venv/bin/python3 -c \"from tools.job_list import job_list; print(job_list.invoke({}))\""
    output = run_command(cmd)
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print("Could not find payload in job_list response.")
        return None
    return match.group(1).strip()

def run_job_kill_tool(job_id):
    cmd = f".venv/bin/python3 -c \"from tools.job_kill import job_kill; print(job_kill.invoke({{'job_id': '{job_id}'}}))\""
    output = run_command(cmd)
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print("Could not find payload in job_kill response.")
        return None
    return match.group(1).strip()

def run_agent_call_tool(agent_id, prompt, run_async=False):
    # Escape single quotes in prompt for python string
    escaped_prompt = prompt.replace("'", "\\'")
    cmd = f".venv/bin/python3 -c \"import asyncio; from tools.agent_call import agent_call; print(asyncio.run(agent_call.ainvoke({{'agent_id': '{agent_id}', 'prompt': '{escaped_prompt}', 'run_async': {run_async}}})))\""
    output = run_command(cmd)
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print(f"Could not find payload in agent_call response. Output: {output}")
        return None
    return match.group(1).strip()

def zombie_hunter():
    print("=== Starting Zombie Hunter ===")
    payload_str = run_job_list_tool()
    if not payload_str:
        print("=== Ending Zombie Hunter ===")
        return

    try:
        jobs = ast.literal_eval(payload_str)
    except Exception as e:
        print(f"Error parsing payload: {e}")
        print("=== Ending Zombie Hunter ===")
        return

    for job in jobs:
        job_id = job.get('job_id')
        agent_id = job.get('agent_id')
        started_str = job.get('started')
        
        if not job_id or not started_str:
            continue
            
        try:
            started_dt = datetime.datetime.strptime(started_str, '%Y-%m-%d %H:%M:%S')
            duration = (datetime.datetime.now() - started_dt).total_seconds()
        except Exception as e:
            print(f"Error calculating duration for job {job_id}: {e}")
            continue
            
        if duration > MAX_JOB_DURATION_SECONDS:
            print(f"Zombie detected: Job {job_id} running for {duration} seconds.")
            
            # Kill the job
            run_job_kill_tool(job_id)
            
            # Find issues assigned to the frozen agent
            gh_output = run_gh_tool(f"issue list --assignee {agent_id} --json number")
            if gh_output:
                try:
                    issues = json.loads(gh_output)
                    for issue in issues:
                        num = issue.get('number')
                        # Remove assignment
                        run_gh_tool(f"issue edit {num} --remove-assignee {agent_id}")
                        # Add comment
                        comment = "System: Agent process terminated due to timeout. Issue re-queued for a fresh agent."
                        run_gh_tool(f"issue comment {num} --body \"{comment}\"")
                        # Add label
                        run_gh_tool(f"issue edit {num} --add-label \"ready-for-dev\"")
                except Exception as e:
                    print(f"Error processing issues for agent {agent_id}: {e}")

    print("=== Ending Zombie Hunter ===")

def tdd_blocked_rescuer():
    print("=== Starting TDD Blocked Rescuer ===")
    output = run_gh_tool("issue list --label \"status: blocked\" --json number,assignees")
    if not output:
        print("Failed to list blocked issues.")
        print("=== Ending TDD Blocked Rescuer ===")
        return
        
    try:
        issues = json.loads(output)
        for issue in issues:
            num = issue.get('number')
            assignees = issue.get('assignees', [])
            logins = [a.get('login') for a in assignees if a.get('login')]
            
            if logins:
                logins_str = ",".join(logins)
                run_gh_tool(f"issue edit {num} --remove-assignee {logins_str}")
                
            run_gh_tool(f"issue edit {num} --remove-label \"status: blocked\" --add-label \"ready-for-dev\"")
            comment = "System: Previous agent hit TDD 3-strike limit. Issue reset and ready for a fresh agent."
            run_gh_tool(f"issue comment {num} --body \"{comment}\"")
            
    except Exception as e:
        print(f"Error processing TDD blocked issues: {e}")
        
    print("=== Ending TDD Blocked Rescuer ===")

def qa_deadlock_rescuer():
    print("=== Starting QA Deadlock Rescuer ===")
    output = run_gh_tool("issue list --label \"status: blocked - QA failed\" --json number,assignees")
    if not output:
        print("Failed to list QA failed issues.")
        print("=== Ending QA Deadlock Rescuer ===")
        return
        
    try:
        issues = json.loads(output)
        for issue in issues:
            num = issue.get('number')
            assignees = issue.get('assignees', [])
            logins = [a.get('login') for a in assignees if a.get('login')]
            
            if logins:
                logins_str = ",".join(logins)
                run_gh_tool(f"issue edit {num} --remove-assignee {logins_str}")
                
            run_gh_tool(f"issue edit {num} --remove-label \"status: blocked - QA failed\" --add-label \"ready-for-dev\"")
            comment = "System: Implementation failed QA 3 times. Issue reset for a fresh architectural approach."
            run_gh_tool(f"issue comment {num} --body \"{comment}\"")
            
    except Exception as e:
        print(f"Error processing QA failed issues: {e}")
        
    print("=== Ending QA Deadlock Rescuer ===")

def spawn_coder_if_needed():
    print("=== Starting Coder Spawner ===")
    
    # 1. Check active jobs
    payload_str = run_job_list_tool()
    if not payload_str:
        print("Failed to get job list.")
        print("=== Ending Coder Spawner ===")
        return
        
    try:
        jobs = ast.literal_eval(payload_str)
        active_jobs_count = len(jobs)
    except Exception as e:
        print(f"Error parsing job list: {e}")
        print("=== Ending Coder Spawner ===")
        return
        
    print(f"Active jobs: {active_jobs_count}/{MAX_CONCURRENT_JOBS}")
    
    if active_jobs_count >= MAX_CONCURRENT_JOBS:
        print("Max concurrent jobs reached. No new agents spawned.")
        print("=== Ending Coder Spawner ===")
        return
        
    # 2. Find ready-for-dev issue
    gh_output = run_gh_tool("issue list --label \"ready-for-dev\" --json number")
    if not gh_output:
        print("Failed to list ready-for-dev issues.")
        print("=== Ending Coder Spawner ===")
        return
        
    try:
        issues = json.loads(gh_output)
        if not issues:
            print("No ready-for-dev issues found.")
            print("=== Ending Coder Spawner ===")
            return
            
        issue_number = issues[0].get('number')
        print(f"Selected issue {issue_number} for spawning.")
        
        # 3. Spawn software-coder
        prompt = f"Please take issue {issue_number}"
        spawn_output = run_agent_call_tool('software-coder', prompt, run_async=True)
        print(f"Spawn result: {spawn_output}")
        
    except Exception as e:
        print(f"Error in coder spawner: {e}")
        
    print("=== Ending Coder Spawner ===")

if __name__ == "__main__":
    zombie_hunter()
    tdd_blocked_rescuer()
    qa_deadlock_rescuer()
    spawn_coder_if_needed()
