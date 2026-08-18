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

    if any(w in f_lower for w in [
        "copy", "caption", "post", "hashtag", "hashtags", "vocabulary", "cantonese",
        "pronunciation", "description", "words", "text"
    ]):
        return "revise_copy"

    if any(w in f_lower for w in [
        "overlay", "font", "subtitle", "subtitles", "sub", "subs", "audio", "sound",
        "volume", "music", "track", "remix"
    ]):
        return "revise_remix"

    if any(w in f_lower for w in [
        "video", "motion", "camera", "movement", "animation", "visual", "plate", "render"
    ]):
        return "revise_video"

    return "revise_copy"
