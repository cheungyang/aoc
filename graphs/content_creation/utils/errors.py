import re

QUOTA_ERROR_PATTERNS = [
    r"\b429\b",
    r"quota",
    r"resource_exhausted",
    r"resourceexhausted",
    r"insufficient_quota",
    r"rate[_\s-]*limit",
    r"billing",
    r"credit[_\s-]*limit",
    r"credits[_\s-]*exhausted",
    r"exceeded\s+(?:your\s+)?(?:current\s+)?quota",
    r"too\s+many\s+requests"
]

QUOTA_REGEX = re.compile("|".join(QUOTA_ERROR_PATTERNS), re.IGNORECASE)


def is_quota_exceeded_error(error_text: str) -> bool:
    """Returns True if the given error string matches known quota/rate limit/billing exhaustion patterns."""
    if not error_text:
        return False
    return bool(QUOTA_REGEX.search(str(error_text)))


def format_quota_exceeded_message(service_name: str, raw_error: str, topic: str = "") -> str:
    """Formats a prominent, clear user-facing explanation when API quota is exhausted."""
    topic_str = f" for topic `{topic}`" if topic else ""
    return (
        f"🛑 **[PIPELINE HALTED: API Quota Exceeded / Rate Limit (429)]**\n\n"
        f"Execution{topic_str} has halted because the API service (`{service_name}`) returned a quota/billing error:\n"
        f"> {raw_error.strip()}\n\n"
        f"### ⚠️ Why this pipeline cannot complete in this state:\n"
        f"The workflow requires generating media assets via `{service_name}`. "
        f"Because the API quota or billing limit is exhausted, automatic retries will not succeed.\n\n"
        f"### 💾 State Safely Preserved:\n"
        f"All existing assets and thread checkpoints are safely preserved. "
        f"You will not lose previous progress (such as approved images or video plots).\n\n"
        f"### 🛠️ Next Steps:\n"
        f"1. Check your API quota, plan, or billing credits for `{service_name}`.\n"
        f"2. Once resolved, reply **'retry'** to resume execution from this exact checkpoint.\n"
        f"3. Or reply **'abort'** to stop the workflow."
    )
