import os
import subprocess
import shlex
import json
from langchain_core.tools import tool
from core.util import format_tool_response

@tool
def onepassword(search_term: str) -> str:
    """
    Access username, password, and OTP from 1Password by searching the title.
    Args:
        search_term: The title or name of the item to search for.
    Returns:
        The username, password, and OTP if found.
    """
    op_bin = "/usr/local/bin/op"

    if not os.path.exists(op_bin):
        return format_tool_response("onepassword", payload="", errors=f"Error: 1Password CLI not found at {op_bin}. Please ensure it is installed.")

    try:
        # Step 1: Get item details and UUID
        cmd1 = [op_bin, "item", "get", search_term, "--format", "json"]
        res1 = subprocess.run(cmd1, capture_output=True, text=True, check=False)

        if res1.returncode != 0:
            return format_tool_response("onepassword", payload="", errors=f"Error finding item: {res1.stderr}")

        try:
            item_data = json.loads(res1.stdout)
        except json.JSONDecodeError:
            return format_tool_response("onepassword", payload="", errors=f"Error decoding JSON from initial search: {res1.stdout}")

        uuid = item_data.get("id")
        if not uuid:
            return format_tool_response("onepassword", payload="", errors="Error: Could not resolve item UUID.")

        username = None
        # Try to extract username from the initial call
        for field in item_data.get("fields", []):
            if field.get("purpose") == "USERNAME" or field.get("label", "").lower() == "username":
                username = field.get("value")
                break

        # Step 2: Get Password using UUID and --reveal
        password = None
        cmd2 = [op_bin, "item", "get", uuid, "--reveal", "--format", "json"]
        res2 = subprocess.run(cmd2, capture_output=True, text=True, check=False)
        
        if res2.returncode == 0:
            try:
                pass_data = json.loads(res2.stdout)
                for field in pass_data.get("fields", []):
                    if field.get("purpose") == "PASSWORD" or field.get("label", "").lower() == "password":
                        password = field.get("value")
                        break
            except json.JSONDecodeError:
                pass # Fallback handled by checking if password is still None

        # Step 3: Get OTP using UUID and --otp
        otp = None
        cmd3 = [op_bin, "item", "get", uuid, "--otp"]
        res3 = subprocess.run(cmd3, capture_output=True, text=True, check=False)
        
        if res3.returncode == 0:
            otp = res3.stdout.strip()

        output = []
        if username:
            output.append(f"username: {username}")
        if password:
            output.append(f"password: {password}")
        if otp:
            output.append(f"otp: {otp}")

        if not output:
            return format_tool_response("onepassword", payload="No username, password, or OTP found for this item.", errors="None")

        return format_tool_response("onepassword", payload="\n".join(output), errors="None")

    except Exception as e:
        return format_tool_response("onepassword", payload="", errors=f"Error performing 1Password action: {e}")
