from dataclasses import dataclass
from typing import Optional, Dict, List
import re


def _extract_poll(text: str) -> tuple[str, Optional[Dict]]:
    """Extracts and strips <poll> XML block from text."""
    poll_match = re.search(r'<poll(.*?)>(.*?)</poll>', text, re.DOTALL)
    if not poll_match:
        return text, None

    poll_xml = poll_match.group(0)
    cleaned_text = text.replace(poll_xml, "").strip()

    poll_attrs = poll_match.group(1)
    poll_content = poll_match.group(2)
    allow_multiple = 'allow_multiple="true"' in poll_attrs

    poll_data = {
        "question": "",
        "allow_multiple": allow_multiple,
        "options": []
    }

    question_match = re.search(r'<question>(.*?)</question>', poll_content, re.DOTALL)
    if question_match:
        poll_data["question"] = question_match.group(1).strip()

    options_match = re.search(r'<options>(.*?)</options>', poll_content, re.DOTALL)
    if options_match:
        options_content = options_match.group(1)
        for opt_match in re.finditer(r'<option>(.*?)</option>', options_content, re.DOTALL):
            opt_content = opt_match.group(1)
            opt_data = {}
            for field in ["text", "emoji", "response"]:
                f_match = re.search(f'<{field}>(.*?)</{field}>', opt_content, re.DOTALL)
                opt_data[field] = f_match.group(1).strip() if f_match else ""
            poll_data["options"].append(opt_data)

    return cleaned_text, poll_data


def _extract_images(text: str) -> tuple[str, Optional[List[str]]]:
    """Extracts and strips <images> XML block from text."""
    images_match = re.search(r'<images>(.*?)</images>', text, re.DOTALL)
    if not images_match:
        return text, None

    images_xml = images_match.group(0)
    cleaned_text = text.replace(images_xml, "").strip()

    image_paths = []
    images_content = images_match.group(1)
    for img_match in re.finditer(r'<image\s+path="(.*?)"\s*/>', images_content):
        image_paths.append(img_match.group(1))

    return cleaned_text, image_paths


def _extract_videos(text: str) -> tuple[str, Optional[List[str]]]:
    """Extracts and strips <videos> XML block from text."""
    videos_match = re.search(r'<videos>(.*?)</videos>', text, re.DOTALL)
    if not videos_match:
        return text, None

    videos_xml = videos_match.group(0)
    cleaned_text = text.replace(videos_xml, "").strip()

    video_paths = []
    videos_content = videos_match.group(1)
    for vid_match in re.finditer(r'<video\s+path="(.*?)"\s*/>', videos_content):
        video_paths.append(vid_match.group(1))

    return cleaned_text, video_paths


def _extract_system_memory_log(text: str) -> tuple[str, Optional[str]]:
    """Extracts and strips <system_memory_log> XML block from text."""
    memory_match = re.search(r'<system_memory_log>(.*?)</system_memory_log>', text, re.DOTALL)
    if not memory_match:
        return text, None

    memory_xml = memory_match.group(0)
    cleaned_text = text.replace(memory_xml, "").strip()
    system_memory_log = memory_match.group(1).strip()

    return cleaned_text, system_memory_log


@dataclass
class AgentResponse:
    text: str
    poll_data: Optional[Dict] = None
    image_paths: Optional[List[str]] = None
    video_paths: Optional[List[str]] = None
    system_memory_log: Optional[str] = None

    @classmethod
    def from_string(cls, reply_text: str) -> 'AgentResponse':
        text = reply_text
        text, poll_data = _extract_poll(text)
        text, image_paths = _extract_images(text)
        text, video_paths = _extract_videos(text)
        text, system_memory_log = _extract_system_memory_log(text)

        return cls(
            text=text,
            poll_data=poll_data,
            image_paths=image_paths,
            video_paths=video_paths,
            system_memory_log=system_memory_log,
        )
