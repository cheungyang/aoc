import re

def classify_gate1_intent(feedback: str) -> str:
    f_lower = feedback.lower().strip()
    if not f_lower:
        return "approved"

    approval_phrases = [
        "approved", "looks good", "looks great", "looks awesome", "looks nice",
        "lgtm", "yes", "go ahead", "proceed", "ok", "okay",
        "sure", "fine", "pass", "good", "great", "yep", "yeah", "accept", "done", "perfect"
    ]
    if any(p == f_lower or f_lower.startswith(p) or f_lower.endswith(p) for p in approval_phrases):
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

    approval_phrases = [
        "approved", "looks good", "looks great", "looks awesome", "looks nice",
        "lgtm", "yes", "go ahead", "proceed", "finalize", "done",
        "ok", "okay", "sure", "fine", "pass", "good", "great", "yep", "yeah", "accept", "perfect"
    ]
    if any(p == f_lower or f_lower.startswith(p) or f_lower.endswith(p) for p in approval_phrases):
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
