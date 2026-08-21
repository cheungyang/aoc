import re

APPROVAL_PHRASES = [
    "approved", "approve", "approves", "approval",
    "looks good", "looks great", "looks awesome", "looks nice", "looks perfect",
    "lgtm", "yes", "go ahead", "proceed", "ok", "okay",
    "sure", "fine", "pass", "passed", "good", "great", "yep", "yeah",
    "accept", "accepted", "done", "perfect", "continue", "confirm", "confirmed",
    "ship", "ship it", "good to go", "ready", "finalize", "finalized"
]

APPROVAL_REGEX = re.compile(
    r'\b(approve|approved|approves|approval|lgtm|proceed|ok|okay|yes|yep|yeah|pass|passed|confirm|confirmed|continue|next|ship|finalize|perfect|good to go|looks good|looks great)\b',
    re.IGNORECASE
)

NEGATION_REGEX = re.compile(
    r'\b(don\'?t|not|cannot|can\'?t|never|disapprove|reject|rejection|no)\b',
    re.IGNORECASE
)

REVISION_TRIGGERS = [
    "change", "revise", "modify", "update", "replace", "fix",
    "re-generate", "regenerate", "re-render", "rerender", "redo", "instead"
]


def classify_gate1_intent(feedback: str) -> str:
    f_lower = feedback.lower().strip()
    if not f_lower:
        return "approved"

    # Direct phrase matching
    if any(p == f_lower or f_lower.startswith(p) or f_lower.endswith(p) for p in APPROVAL_PHRASES):
        return "approved"

    # Regex approval check if no negation is present
    has_approval_word = bool(APPROVAL_REGEX.search(f_lower))
    has_negation = bool(NEGATION_REGEX.search(f_lower))
    has_revision_trigger = any(w in f_lower for w in REVISION_TRIGGERS)

    if has_approval_word and not has_negation and not has_revision_trigger:
        return "approved"

    if any(w in f_lower for w in [
        "plot", "motion", "camera", "movement", "script", "animation", "audio sync",
        "mouth", "articulation", "phonetic", "timing", "seconds", "duration"
    ]):
        return "revise_plot"

    if any(w in f_lower for w in [
        "image", "picture", "photo", "drawing", "art", "character", "costume", "outfit",
        "wear", "wearing", "clothes", "dress", "pose", "posing", "color",
        "background", "style", "scene", "reference", "illustration", "visual", "visuals",
        "render", "hair", "face", "eyes", "crawl", "crawling", "onesie", "animal", "toddler",
        "behave", "hat", "shoes"
    ]):
        return "revise_image"

    return "revise_image"


def classify_gate2_intent(feedback: str) -> str:
    f_lower = feedback.lower().strip()
    if not f_lower:
        return "approved"

    # Direct phrase matching
    if any(p == f_lower or f_lower.startswith(p) or f_lower.endswith(p) for p in APPROVAL_PHRASES):
        return "approved"

    # Regex approval check if no negation is present
    has_approval_word = bool(APPROVAL_REGEX.search(f_lower))
    has_negation = bool(NEGATION_REGEX.search(f_lower))
    has_revision_trigger = any(w in f_lower for w in REVISION_TRIGGERS)

    if has_approval_word and not has_negation and not has_revision_trigger:
        return "approved"

    # Remix / Audio / Subtitle / Overlay check (prioritized to prevent 'text' in feedback from false matching copy)
    if any(w in f_lower for w in [
        "remix", "overlay", "font", "subtitle", "subtitles", "sub", "subs", "audio", "sound",
        "volume", "music", "track"
    ]):
        return "revise_remix"

    if any(w in f_lower for w in [
        "video", "motion", "camera", "movement", "animation", "visual", "plate", "render"
    ]):
        return "revise_video"

    if any(w in f_lower for w in [
        "copy", "caption", "post", "hashtag", "hashtags", "vocabulary", "cantonese",
        "pronunciation", "description", "words", "text"
    ]):
        return "revise_copy"

    return "revise_copy"


def extract_remix_parameters(feedback: str) -> dict:
    """Extracts dynamic audio timing, subtitle timing, font size, and position from human feedback."""
    params = {}
    if not feedback:
        return params

    f_clean = feedback.strip()

    # 1. Extract audio start time: e.g. "audio should be inserted at 4s", "audio at 4s", "audio start at 2.5s", "audio: 3s"
    audio_m = re.search(
        r'(?:audio|sound|track)\s+(?:should\s+(?:also\s+)?(?:be\s+)?(?:inserted\s+)?at|starts?\s+at|inserted\s+at|at|from|timing[:\s]+)?\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b',
        f_clean,
        re.IGNORECASE
    )
    if audio_m:
        try:
            params["audio_start_time"] = float(audio_m.group(1))
        except (ValueError, TypeError):
            pass

    # 2. Extract subtitle / text start time: e.g. "subtitles should also appear at 4s", "text should also appear at 4s", "subtitles at 4s", "text at 4s"
    text_start_m = re.search(
        r'(?:subtitles?|subs?|overlay|text)\s+(?:should\s+(?:also\s+)?(?:appear|be\s+inserted)\s+at|starts?\s+at|inserted\s+at|at|from|timing[:\s]+)?\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b',
        f_clean,
        re.IGNORECASE
    )
    if text_start_m:
        try:
            params["text_start_time"] = float(text_start_m.group(1))
        except (ValueError, TypeError):
            pass

    # 3. Extract subtitle end time or duration: e.g. "until 6s", "end at 5s", "to 7s", "duration 3s"
    text_end_m = re.search(
        r'(?:until|to|end\s+at|ends?\s+at)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b',
        f_clean,
        re.IGNORECASE
    )
    if text_end_m:
        try:
            params["text_end_time"] = float(text_end_m.group(1))
        except (ValueError, TypeError):
            pass

    # 4. Extract font size: e.g. "font size 60", "font size: 72", "size 54", "fontsize 48"
    font_size_m = re.search(
        r'(?:font\s*size|fontsize|size)[:\s]+(\d+)\b',
        f_clean,
        re.IGNORECASE
    )
    if font_size_m:
        try:
            params["font_size"] = int(font_size_m.group(1))
        except (ValueError, TypeError):
            pass

    # 5. Extract position: e.g. "position: top", "at the top", "position bottom", "center", "middle"
    if re.search(r'\b(top|upper)\b', f_clean, re.IGNORECASE):
        params["position"] = "top"
    elif re.search(r'\b(bottom|lower)\b', f_clean, re.IGNORECASE):
        params["position"] = "bottom"
    elif re.search(r'\b(center|middle)\b', f_clean, re.IGNORECASE):
        params["position"] = "center"

    # 6. Extract font color: e.g. "yellow", "white", "red", "green", "blue", "black", "gold"
    color_m = re.search(r'\b(yellow|white|red|green|blue|black|cyan|magenta|orange|gold)\b', f_clean, re.IGNORECASE)
    if color_m and "color" in f_clean.lower():
        params["font_color"] = color_m.group(1).lower()

    return params

