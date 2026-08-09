#!/usr/bin/env python3
"""
Diagnostic utility to verify LangSmith onboarding and connectivity.
Run with: python scripts/verify_langsmith.py
"""

import os
import sys
from dotenv import load_dotenv

# Ensure root directory is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def verify_langsmith():
    print("=" * 60)
    print("LangSmith Onboarding & Connectivity Verification")
    print("=" * 60)

    # 1. Load environment variables
    load_dotenv()

    # 2. Check Tracing Flag
    tracing_enabled = os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", "false")).lower() in ("true", "1", "yes", "t")
    print(f"\n[1] Tracing Flag (LANGSMITH_TRACING): {'ENABLED (true)' if tracing_enabled else 'DISABLED (false)'}")

    # 3. Check API Key
    api_key = os.getenv("LANGSMITH_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""))
    masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else ("SET" if api_key else "NOT SET")
    print(f"[2] API Key (LANGSMITH_API_KEY): {masked_key}")

    # 4. Check Project & Endpoint
    project = os.getenv("LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "default"))
    endpoint = os.getenv("LANGSMITH_ENDPOINT", os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"))
    workspace_id = os.getenv("LANGSMITH_WORKSPACE_ID", "")
    print(f"[3] Project (LANGSMITH_PROJECT): '{project}'")
    print(f"[4] Endpoint (LANGSMITH_ENDPOINT): '{endpoint}'")
    if workspace_id:
        print(f"[5] Workspace ID (LANGSMITH_WORKSPACE_ID): '{workspace_id}'")

    # 5. Check Package Installation
    print("\nChecking dependencies...")
    try:
        import langsmith
        from langsmith import Client, traceable
        print(f"  ✓ langsmith is installed (version: {getattr(langsmith, '__version__', 'unknown')})")
    except ImportError as e:
        print(f"  ✗ langsmith package missing: {e}")
        print("    Install it with: pip install langsmith")
        return False

    try:
        import langgraph
        print(f"  ✓ langgraph is installed (version: {getattr(langgraph, '__version__', 'unknown')})")
    except ImportError as e:
        print(f"  ✗ langgraph package missing: {e}")
        return False

    if not tracing_enabled:
        print("\n" + "=" * 60)
        print("STATUS: LangSmith tracing is currently disabled.")
        print("To enable tracing, set in your .env file:")
        print("  LANGSMITH_TRACING=true")
        print("  LANGSMITH_API_KEY=<your-api-key>")
        print("  LANGSMITH_PROJECT=<your-project-name>")
        print("=" * 60)
        return True

    if not api_key or api_key == "your_langsmith_api_key_here":
        print("\n" + "=" * 60)
        print("STATUS: LANGSMITH_TRACING is true, but LANGSMITH_API_KEY is missing or invalid.")
        print("Please add your LangSmith API key from https://smith.langchain.com/settings into .env")
        print("=" * 60)
        return False

    # 6. Test Client Connectivity
    print("\nTesting LangSmith API connectivity...")
    try:
        client = Client(api_url=endpoint, api_key=api_key)
        # Verify read access
        projects = list(client.list_projects(limit=1))
        print(f"  ✓ Successfully connected to LangSmith API at {endpoint}")
    except Exception as e:
        print(f"  ✗ Failed to connect to LangSmith API: {e}")
        print("    Please verify that:")
        print("    - Your LANGSMITH_API_KEY is correct")
        print("    - Your LANGSMITH_ENDPOINT matches your account region (US vs EU vs APAC)")
        return False

    # 7. Test Tracing Trace
    print("\nEmitting test trace to project '{project}'...".format(project=project))
    try:
        @traceable(name="langsmith_verification_test", project_name=project, tags=["diagnostic", "test"])
        def sample_test_trace(message: str) -> dict:
            return {"status": "ok", "message": f"Verification successful: {message}"}

        result = sample_test_trace("Testing LangSmith integration")
        print(f"  ✓ Test trace emitted successfully! Result: {result.get('message')}")
    except Exception as e:
        print(f"  ✗ Failed to emit test trace: {e}")
        return False

    print("\n" + "=" * 60)
    print("SUCCESS: LangSmith is fully configured and operational!")
    print(f"View traces in your LangSmith project dashboard: https://smith.langchain.com")
    print("=" * 60)
    return True

if __name__ == "__main__":
    success = verify_langsmith()
    sys.exit(0 if success else 1)
