import os
import aiohttp
import re
from urllib.parse import urlparse, unquote
from langchain_core.messages import AIMessage
from graphs.content_creation.utils.paths import normalize_path

AUDIO_EXTENSIONS = ('.m4a', '.wav', '.mp3', '.ogg', '.aac', '.flac')

async def ask_for_audio_node(state: dict) -> dict:
    """Asks the user to upload an audio clip."""
    return {
        "messages": [AIMessage(content="Please upload the audio clip (m4a or wav) for the new word.")]
    }

async def ingest_audio_node(state: dict) -> dict:
    """Macro-Node 1: Ingests audio from message attachments, query parameters, or existing directory files."""
    if state.get("error_message"):
        return {}

    project_path = state.get("project_path")
    output_path = state.get("output_path")

    if not project_path or not output_path:
        return {
            "error_message": "Missing required project/output path. Both 'project_path' and 'output_path' must be explicitly provided."
        }

    os.makedirs(output_path, exist_ok=True)
    topic = str(state.get("topic") or state.get("word") or "").strip().lower()

    # 1. If state already points to a valid audio file on disk, pass it through directly
    for k in ["source_audio_path", "audio_file", "audio"]:
        val = state.get(k)
        if val and isinstance(val, str) and os.path.isfile(val) and val.lower().endswith(AUDIO_EXTENSIONS):
            return {"source_audio_path": val, "project_path": project_path, "output_path": output_path}

    # 2. Collect candidate text sources (query, latest messages)
    candidate_texts = []
    if state.get("query"):
        candidate_texts.append(str(state["query"]))
    messages = state.get("messages", [])
    if isinstance(messages, list):
        for msg in reversed(messages):
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                content = " ".join([c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"])
            elif not isinstance(content, str):
                content = str(content)
            if content:
                candidate_texts.append(content)

    # 3. Search candidate texts for URLs or local paths
    for text in candidate_texts:
        # Pattern A: Markdown attachment [Attached file: filename.m4a](https://...)
        m_attached = re.search(r'\[Attached file:\s*([^\]]+)\]\((https?://[^\)]+)\)', text, re.IGNORECASE)
        if m_attached:
            filename = m_attached.group(1).strip()
            url = m_attached.group(2).strip()
            if filename.lower().endswith(AUDIO_EXTENSIONS) or any(ext in url.lower() for ext in AUDIO_EXTENSIONS):
                res = await _download_audio(url, filename, output_path)
                if res:
                    res["project_path"] = project_path
                    res["output_path"] = output_path
                    return res

        # Pattern B: Direct URL containing audio extension (e.g. Discord CDN or web URL)
        m_url = re.search(r'(https?://[^\s"\'<>]+(?:\.m4a|\.wav|\.mp3|\.ogg|\.aac|\.flac)(?:\?[^\s"\'<>]*)?)', text, re.IGNORECASE)
        if m_url:
            url = m_url.group(1).strip()
            parsed_path = unquote(urlparse(url).path)
            filename = os.path.basename(parsed_path) or f"{topic or 'audio'}_clip.m4a"
            res = await _download_audio(url, filename, output_path)
            if res:
                res["project_path"] = project_path
                res["output_path"] = output_path
                return res

        # Pattern C: Key-value pattern: audio: /path/to/file or audio_file: https://...
        m_kv = re.search(r'(?:source_audio_path|audio_file|audio)[:=]\s*["\']?([^"\'\s,]+)["\']?', text, re.IGNORECASE)
        if m_kv:
            target = m_kv.group(1).strip()
            if target.startswith("http://") or target.startswith("https://"):
                parsed_path = unquote(urlparse(target).path)
                filename = os.path.basename(parsed_path) or f"{topic or 'audio'}_clip.m4a"
                res = await _download_audio(target, filename, output_path)
                if res:
                    res["project_path"] = project_path
                    res["output_path"] = output_path
                    return res
            elif os.path.isfile(target) and target.lower().endswith(AUDIO_EXTENSIONS):
                return {"source_audio_path": target, "project_path": project_path, "output_path": output_path}

        # Pattern D: Local file path mentioned in text
        for line in text.splitlines():
            cleaned_line = line.strip().strip("'\"`")
            if cleaned_line.lower().endswith(AUDIO_EXTENSIONS) and os.path.isfile(cleaned_line):
                return {"source_audio_path": cleaned_line, "project_path": project_path, "output_path": output_path}

    # 4. Check if an audio file already exists in output_path (or project_path)
    target_dirs = [d for d in [output_path, project_path] if d and os.path.isdir(d)]
    for d in target_dirs:
        for fname in os.listdir(d):
            if fname.lower().endswith(AUDIO_EXTENSIONS) and not fname.startswith("."):
                fpath = os.path.join(d, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    return {"source_audio_path": fpath, "project_path": project_path, "output_path": output_path}

    return {"project_path": project_path, "output_path": output_path}

async def _download_audio(url: str, filename: str, target_dir: str) -> dict:
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
    audio_path = os.path.join(target_dir, filename) if target_dir else filename
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.read()
                    if data:
                        with open(audio_path, 'wb') as f:
                            f.write(data)
                        return {"source_audio_path": audio_path}
    except Exception as e:
        print(f"ingest_audio_node: Error downloading audio from {url}: {e}")
    return {}
