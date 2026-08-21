import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

def _append_execution_log(
    output_path: Optional[str] = None,
    topic: str = "scene",
    actor: str = "",
    event_title: str = "",
    details: Dict[str, Any] = None,
    log_path: Optional[str] = None
):
    """Continuously appends timestamped markdown audit log entries to execution_log.md."""
    try:
        details = details or {}
        topic_clean = str(topic or "scene").strip().lower()
        out_dir = str(output_path or "")
        target_file = log_path or (os.path.join(out_dir, "execution_log.md") if out_dir else "")
        if not target_file:
            return
        target_dir = os.path.dirname(target_file)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        is_new = not os.path.exists(target_file)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        time_short = datetime.now(timezone.utc).strftime("%H:%M:%S")

        with open(target_file, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# 📜 Content Creation Trajectory: {topic_clean}\n\n")
                f.write(f"- **Topic**: `{topic_clean}`\n")
                f.write(f"- **Output Path**: `{out_dir}`\n")
                f.write(f"- **Initiated At**: `{now_str}`\n\n")
                f.write("---\n\n")

            f.write(f"## {actor} [{time_short}] — {event_title}\n")
            for k, v in details.items():
                if v is None or v == "":
                    continue
                if isinstance(v, str) and ("\n" in v or len(v) > 80):
                    f.write(f"- **{k}**:\n```markdown\n{v.strip()}\n```\n")
                elif isinstance(v, list):
                    f.write(f"- **{k}**:\n")
                    for item in v:
                        f.write(f"  - `{item}`\n")
                else:
                    f.write(f"- **{k}**: `{v}`\n")
            f.write("\n")
    except Exception as e:
        print(f"ContentCreationGraph: Error appending to execution_log.md: {e}")

