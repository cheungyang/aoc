import re

def classify_gate1_intent(feedback: str) -> str:
    f_lower = feedback.lower().strip()
    if not f_lower or f_lower in ["approved", "looks good", "lgtm", "yes", "go ahead", "proceed"]:
        return "approved"
    if any(w in f_lower for w in ["image", "picture", "photo", "drawing", "art"]):
        return "revise_image"
    if any(w in f_lower for w in ["plot", "motion", "prompt", "movement", "camera", "script", "video"]):
        return "revise_plot"
    return "clarify"

def classify_gate2_intent(feedback: str) -> str:
    f_lower = feedback.lower().strip()
    if not f_lower or f_lower in ["approved", "looks good", "lgtm", "yes", "go ahead", "proceed", "finalize", "done"]:
        return "approved"
    if any(w in f_lower for w in ["copy", "text", "caption", "post", "hashtag", "words"]):
        return "revise_copy"
    if any(w in f_lower for w in ["overlay", "font", "color", "audio", "sound", "volume", "music", "track"]):
        return "revise_remix"
    if any(w in f_lower for w in ["video", "motion", "camera", "movement", "animation"]):
        return "revise_video"
    return "clarify"
