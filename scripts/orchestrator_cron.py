#!/usr/bin/env python3
import subprocess
import json
import datetime
import ast
import re
import sys
import asyncio
import os

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tools.job_list import job_list
from tools.agent_call import agent_call
from tools.gh import gh

def run_job_list_tool():
    output = job_list.invoke({})
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print("Could not find payload in job_list response.")
        return None
    return match.group(1).strip()

def run_agent_call_tool(agent_id, prompt, run_async=False):
    output = asyncio.run(agent_call.ainvoke({"agent_id": agent_id, "prompt": prompt, "run_async": run_async}))
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print(f"Could not find payload in agent_call response. Output: {output}")
        return None
    return match.group(1).strip()

def run_gh_tool(command):
    output = gh.invoke({"command": command})
    if not output:
        return None
    match = re.search(r"<payload>(.*?)</payload>", output, re.DOTALL)
    if not match:
        print(f"Could not find payload in gh tool response. Output: {output}")
        return None
    return match.group(1).strip()

def check_github():
    has_work = False
    
    try:
        payload = run_gh_tool("pr list --state open")
        if payload and payload.strip():
            print("Open PRs found.")
            has_work = True
    except Exception as e:
        print(f"Error checking PRs: {e}")
        
    try:
        payload = run_gh_tool("issue list --state open")
        if payload and payload.strip():
            print("Open Issues found.")
            has_work = True
    except Exception as e:
        print(f"Error checking Issues: {e}")
        
    return has_work



def check_watchdog():
    has_work = False
    payload_str = run_job_list_tool()
    if not payload_str:
        return False
        
    try:
        jobs = ast.literal_eval(payload_str)
    except Exception as e:
        print(f"Error parsing job list: {e}")
        return False
        
    for job in jobs:
        started_str = job.get('started')
        status = job.get('status')
        
        if not started_str or status != 'running':
            continue
            
        try:
            started_dt = datetime.datetime.strptime(started_str, '%Y-%m-%d %H:%M:%S')
            duration = (datetime.datetime.now() - started_dt).total_seconds()
            # Requirement B: current_time - job.started > 1800
            if duration > 1800:
                print(f"Stuck job detected: {job.get('job_id')} running for {duration} seconds.")
                has_work = True
                break # One stuck job is enough
        except Exception as e:
            print(f"Error checking job duration: {e}")
            
    return has_work

def main():
    print("=== Orchestrator Cron Started ===")
    
    has_work_gh = check_github()
    has_work_watchdog = check_watchdog()
    
    has_work = has_work_gh or has_work_watchdog
    
    if not has_work:
        print("No work found. Terminating silently.")
        return
        
    print("Work detected. Waking up agent.")
    
    # Asynchronously call Concierge (id: main)
    prompt = "Execute the software_orchestration skill."
    result = run_agent_call_tool("main", prompt)
    print(f"Agent call result: {result}")
    
    print("=== Orchestrator Cron Finished ===")

if __name__ == "__main__":
    main()
