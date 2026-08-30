import re
import os
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from core.util.config import Config


def extract_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """
    Extracts YAML-like frontmatter from markdown content if present.
    Returns (frontmatter_dict, remaining_content).
    """
    frontmatter = {}
    remaining_content = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            remaining_content = parts[2].lstrip("\n")
            
            # Simple line-by-line key: value parsing
            for line in fm_text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        # Parse list [tag1, tag2]
                        items = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
                        frontmatter[key] = items
                    elif val.lower() in ("true", "false"):
                        frontmatter[key] = val.lower() == "true"
                    else:
                        frontmatter[key] = val.strip("\"'")

    return frontmatter, remaining_content


def extract_inline_tags(text: str) -> List[str]:
    """Extracts inline #tags (e.g. #project, #ai/agents)."""
    tag_matches = re.findall(r"(?:^|\s)#([a-zA-Z0-9_\-\/]+)", text)
    return list(dict.fromkeys(tag_matches))


def extract_title(content: str, filename: str, frontmatter: Dict[str, Any]) -> str:
    """Extracts note title from frontmatter, first # H1 header, or filename."""
    if frontmatter.get("title"):
        return str(frontmatter["title"])

    # Check for # H1
    h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if h1_match:
        return h1_match.group(1).strip()

    base_name = os.path.basename(filename)
    if base_name.endswith(".md"):
        base_name = base_name[:-3]
    return base_name


def split_markdown_into_chunks(
    file_path: str,
    content: str,
    category: str = "vault",
    max_chunk_chars: int = 1500,
    overlap_chars: int = 150
) -> List[Dict[str, Any]]:
    """
    Parses a markdown document into hierarchical header-aware chunks.
    """
    frontmatter, clean_content = extract_frontmatter(content)
    title = extract_title(clean_content, file_path, frontmatter)
    
    # Collect tags from frontmatter and inline
    tags = []
    fm_tags = frontmatter.get("tags") or frontmatter.get("tag") or []
    if isinstance(fm_tags, list):
        tags.extend([str(t).lstrip("#") for t in fm_tags])
    elif isinstance(fm_tags, str):
        tags.extend([t.strip().lstrip("#") for t in fm_tags.split(",") if t.strip()])

    inline_tags = extract_inline_tags(clean_content)
    tags.extend(inline_tags)
    tags = list(dict.fromkeys(tags))

    lines = clean_content.splitlines()
    sections = []
    current_headers = {}  # level (1..6) -> header text
    current_lines = []

    def get_current_breadcrumb():
        ordered = [current_headers[lvl] for lvl in sorted(current_headers.keys())]
        return " > ".join(ordered) if ordered else "General"

    for line in lines:
        header_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if header_match:
            # Save accumulated lines under previous header
            if current_lines:
                text_block = "\n".join(current_lines).strip()
                if text_block:
                    sections.append((get_current_breadcrumb(), text_block))
                current_lines = []

            level = len(header_match.group(1))
            header_text = header_match.group(2).strip()

            # Clear headers at this level or deeper
            levels_to_remove = [lvl for lvl in current_headers if lvl >= level]
            for lvl in levels_to_remove:
                del current_headers[lvl]
            current_headers[level] = header_text
        else:
            current_lines.append(line)

    if current_lines:
        text_block = "\n".join(current_lines).strip()
        if text_block:
            sections.append((get_current_breadcrumb(), text_block))

    # If no sections were found (e.g. empty or only headers)
    if not sections and clean_content.strip():
        sections.append(("General", clean_content.strip()))

    # Build chunk dictionaries
    chunks = []
    chunk_index = 0
    now_iso = datetime.now().isoformat(timespec="seconds")

    for breadcrumb, raw_text in sections:
        # If section is longer than max_chunk_chars, split by paragraphs
        sub_chunks = []
        if len(raw_text) > max_chunk_chars:
            paragraphs = raw_text.split("\n\n")
            curr_p = []
            curr_len = 0
            for p in paragraphs:
                p_str = p.strip()
                if not p_str:
                    continue
                if curr_len + len(p_str) > max_chunk_chars and curr_p:
                    sub_chunks.append("\n\n".join(curr_p))
                    curr_p = [p_str]
                    curr_len = len(p_str)
                else:
                    curr_p.append(p_str)
                    curr_len += len(p_str) + 2
            if curr_p:
                sub_chunks.append("\n\n".join(curr_p))
        else:
            sub_chunks = [raw_text]

        for sub_text in sub_chunks:
            if not sub_text.strip():
                continue

            # Format contextual enriched text
            meta_header = f"Title: {title}\nPath: {file_path}\nCategory: {category}\nSection: {breadcrumb}"
            if tags:
                meta_header += f"\nTags: #{', #'.join(tags)}"
            enriched_text = f"{meta_header}\n\n{sub_text}"

            # Content hash for incremental tracking
            hash_input = f"{file_path}|{category}|{breadcrumb}|{sub_text}"
            c_hash = hashlib.md5(hash_input.encode("utf-8")).hexdigest()
            chunk_id = hashlib.md5(f"{file_path}:{chunk_index}".encode("utf-8")).hexdigest()

            chunks.append({
                "id": chunk_id,
                "file_path": file_path,
                "category": category,
                "title": title,
                "header_path": breadcrumb,
                "tags": json.dumps(tags),
                "text": enriched_text,
                "raw_content": sub_text,
                "content_hash": c_hash,
                "updated_at": now_iso
            })
            chunk_index += 1

    return chunks


def get_embedding_client():
    """
    Returns a GoogleGenerativeAIEmbeddings client if Gemini API key is configured.
    """
    config = Config()
    gemini_key = config.gemini_api_key
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        return None

    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        model = config.embedding_model
        if not model or model == "text-embedding-3-small":
            model = "models/text-embedding-004"
        elif not model.startswith("models/"):
            model = f"models/{model}"
        return GoogleGenerativeAIEmbeddings(model=model, google_api_key=gemini_key)
    except Exception as e:
        print(f"Warning: Could not initialize GoogleGenerativeAIEmbeddings: {e}")
        return None


def generate_embeddings(texts: List[str], client: Optional[Any] = None) -> List[List[float]]:
    """
    Generates dense embeddings for a list of texts.
    If no client is available, generates deterministic pseudo-random vectors for testability.
    """
    if not texts:
        return []

    client = client or get_embedding_client()
    if client:
        try:
            return client.embed_documents(texts)
        except Exception as e:
            print(f"Warning: Embedding call failed: {e}. Falling back to deterministic embeddings.")

    # Deterministic fallback vector generation based on MD5
    dim = Config().embedding_dimensions
    vectors = []
    for text in texts:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat/slice hash bytes to make a float list of length `dim`
        floats = []
        for i in range(dim):
            byte_val = h[(i * 4) % len(h)]
            floats.append((byte_val / 255.0) - 0.5)
        vectors.append(floats)
    return vectors


def generate_query_embedding(query: str, client: Optional[Any] = None) -> List[float]:
    """Generates embedding for a single query text."""
    client = client or get_embedding_client()
    if client:
        try:
            return client.embed_query(query)
        except Exception as e:
            print(f"Warning: Query embedding call failed: {e}. Falling back to deterministic embedding.")

    dim = Config().embedding_dimensions
    h = hashlib.sha256(query.encode("utf-8")).digest()
    floats = []
    for i in range(dim):
        byte_val = h[(i * 4) % len(h)]
        floats.append((byte_val / 255.0) - 0.5)
    return floats
