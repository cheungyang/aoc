import os
import aiohttp
import re
from langchain_core.messages import AIMessage
from graphs.content_creation.utils.paths import normalize_project_path

async def ask_for_audio_node(state: dict):
    """Asks the user for the audio clip of the new word."""
    return {
        "messages": [AIMessage(content="Please upload the audio clip (m4a or wav) for the new word.")]
    }

async def receive_audio_node(state: dict):
    """Processes the user's response to extract and download the audio clip."""
    project_dir = normalize_project_path(state.get("project_dir", ""))
    
    messages = state.get("messages", [])
    if not messages:
        return {}
        
    last_message = messages[-1]
    
    # We only care if the last message is from the user
    if getattr(last_message, "type", "") != "human" and getattr(last_message, "role", "") != "user" and not (hasattr(last_message, "__class__") and last_message.__class__.__name__ == "HumanMessage"):
        return {}
        
    content = last_message.content
    if isinstance(content, list):
        content = " ".join([c.get("text", "") for c in content if c.get("type") == "text"])
    elif not isinstance(content, str):
        content = str(content)
        
    # Look for Discord attachment URL pattern
    match = re.search(r'\[Attached file: ([^\]]+)\]\((https?://[^\)]+)\)', content)
    if match:
        filename = match.group(1)
        url = match.group(2)
        
        # Check if it's audio
        if filename.lower().endswith(('.m4a', '.wav', '.mp3', '.ogg', '.aac')):
            # Ensure project_dir exists
            if project_dir and not os.path.exists(project_dir):
                os.makedirs(project_dir, exist_ok=True)
                
            audio_path = os.path.join(project_dir, filename) if project_dir else filename
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            with open(audio_path, 'wb') as f:
                                f.write(await response.read())
                            
                            return {"source_audio_path": audio_path}
                        else:
                            return {"error_message": f"Failed to download audio from Discord. Status: {response.status}"}
            except Exception as e:
                return {"error_message": f"Error downloading audio: {e}"}
                
    # If the user provided a local path directly
    if content and os.path.exists(content.strip()) and content.strip().lower().endswith(('.m4a', '.wav', '.mp3', '.ogg', '.aac')):
        return {"source_audio_path": content.strip()}
        
    # Not found
    return {}
