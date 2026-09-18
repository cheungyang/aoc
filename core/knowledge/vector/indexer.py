import re
import os
import json
import time
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


# Google retired text-embedding-004: the v1beta endpoint now answers 404 for it,
# which silently demoted every index to the deterministic fallback vectors.
# gemini-embedding-001 is the current general-purpose model and supports
# Matryoshka output dimensions, so an existing 1536-wide table stays valid.
DEFAULT_EMBEDDING_MODEL = "models/gemini-embedding-001"

# Names that must not be sent to the API: either retired, or an OpenAI model
# name left over from the config default.
_RETIRED_EMBEDDING_MODELS = {
    "text-embedding-3-small",
    "text-embedding-004",
    "models/text-embedding-004",
    "embedding-001",
    "models/embedding-001",
}


def resolve_embedding_model(model_name: Optional[str]) -> str:
    """Normalizes a configured embedding model into a servable Gemini model id."""
    model = (model_name or "").strip()
    if not model or model in _RETIRED_EMBEDDING_MODELS:
        return DEFAULT_EMBEDDING_MODEL
    if not model.startswith("models/"):
        model = f"models/{model}"
    if model in _RETIRED_EMBEDDING_MODELS:
        return DEFAULT_EMBEDDING_MODEL
    return model


def get_embedding_client(model_name: Optional[str] = None):
    """
    Returns a GoogleGenerativeAIEmbeddings client if Gemini API key is configured.
    """
    config = Config()
    gemini_key = config.gemini_api_key
    if not gemini_key or gemini_key == "your_gemini_api_key_here":
        return None

    try:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        model = resolve_embedding_model(model_name or config.embedding_model)

        kwargs = {"model": model, "google_api_key": gemini_key}
        if "gemini-embedding" in model:
            kwargs["output_dimensionality"] = config.embedding_dimensions

        return GoogleGenerativeAIEmbeddings(**kwargs)
    except Exception as e:
        print(f"Warning: Could not initialize GoogleGenerativeAIEmbeddings: {e}")
        return None


# Distinguishes "caller didn't say" from "caller said: no client".
# `client or get_embedding_client()` conflated the two, which made every
# offline path a lie: --skip-embedding, whose help text promises 0 quota cost,
# passed client=None and got a live API client built for it anyway.
_AUTO_CLIENT = object()


def _resolve_client(client):
    return get_embedding_client() if client is _AUTO_CLIENT else client


class EmbeddingError(RuntimeError):
    """An embedding client was available but could not produce vectors.

    Deliberately fatal rather than falling back to deterministic vectors. A hash
    of the text is a perfectly well-formed vector that is indistinguishable from
    a real one once written, so substituting it silently poisons the index: the
    affected chunks never match anything meaningful, and nothing records which
    ones they were. A failed index build you have to re-run is a much smaller
    problem than an index that is quietly wrong for months.
    """


EMBEDDING_MAX_ATTEMPTS = 3
EMBEDDING_RETRY_BASE_SECONDS = 1.0

# Substrings that mark a failure as worth retrying. Anything else -- a retired
# model name, a bad key -- will fail identically on the next attempt, so retrying
# only delays the report.
_TRANSIENT_MARKERS = (
    "429", "resource_exhausted", "rate limit", "ratelimit", "quota",
    "500", "503", "internal error", "unavailable", "overloaded",
    "timeout", "timed out", "deadline",
)


def _is_transient(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _deterministic_vector(text: str, dim: int) -> List[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    # Repeat/slice hash bytes to make a float list of length `dim`
    return [(h[(i * 4) % len(h)] / 255.0) - 0.5 for i in range(dim)]


def _embed_with_retry(call, description: str):
    """Runs an embedding call, retrying transient failures with backoff."""
    last_error = None
    attempts = 0

    for attempt in range(1, EMBEDDING_MAX_ATTEMPTS + 1):
        attempts = attempt
        try:
            return call()
        except Exception as e:
            last_error = e
            if not _is_transient(e) or attempt == EMBEDDING_MAX_ATTEMPTS:
                break
            delay = EMBEDDING_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            print(
                f"Warning: {description} failed (attempt {attempt}/{EMBEDDING_MAX_ATTEMPTS}): "
                f"{e}. Retrying in {delay:.1f}s."
            )
            time.sleep(delay)

    raise EmbeddingError(
        f"{description} failed after {attempts} attempt(s): {last_error}"
    ) from last_error


def generate_embeddings(texts: List[str], client: Any = _AUTO_CLIENT) -> List[List[float]]:
    """
    Generates dense embeddings for a list of texts.

    Pass `client=None` to force deterministic offline vectors; omit the argument
    to build a client from config if one is available.

    Raises:
        EmbeddingError: a client was available but the call failed. Never
            silently substitutes deterministic vectors for a failed API call.
    """
    if not texts:
        return []

    client = _resolve_client(client)
    if client is None:
        dim = Config().embedding_dimensions
        return [_deterministic_vector(text, dim) for text in texts]

    return _embed_with_retry(
        lambda: client.embed_documents(texts),
        f"Embedding request for {len(texts)} chunk(s)",
    )


def generate_query_embedding(query: str, client: Any = _AUTO_CLIENT) -> List[float]:
    """Generates embedding for a single query text.

    Pass `client=None` to force a deterministic offline vector. Note that such a
    vector cannot match API-generated document vectors, so callers without a
    client should prefer keyword search over a meaningless vector search.

    Raises:
        EmbeddingError: a client was available but the call failed.
    """
    client = _resolve_client(client)
    if client is None:
        return _deterministic_vector(query, Config().embedding_dimensions)

    return _embed_with_retry(
        lambda: client.embed_query(query),
        "Query embedding request",
    )
