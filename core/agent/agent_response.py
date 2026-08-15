from dataclasses import dataclass
from typing import Optional, Dict, List
import re


@dataclass
class AgentResponse:
    text: str
    poll_data: Optional[Dict] = None
    image_paths: Optional[List[str]] = None
    video_paths: Optional[List[str]] = None

    @classmethod
    def from_string(cls, reply_text: str) -> 'AgentResponse':
        text_content = reply_text
        poll_data = None
        image_paths = None
        video_paths = None
        
        # Find the poll block
        poll_match = re.search(r'<poll(.*?)>(.*?)</poll>', reply_text, re.DOTALL)
        if poll_match:
            poll_xml = poll_match.group(0)
            text_content = text_content.replace(poll_xml, "").strip()
            
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
                        
        # Find the images block
        images_match = re.search(r'<images>(.*?)</images>', reply_text, re.DOTALL)
        if images_match:
            images_xml = images_match.group(0)
            text_content = text_content.replace(images_xml, "").strip()
            
            image_paths = []
            images_content = images_match.group(1)
            for img_match in re.finditer(r'<image\s+path="(.*?)"\s*/>', images_content):
                image_paths.append(img_match.group(1))

        # Find the videos block
        videos_match = re.search(r'<videos>(.*?)</videos>', reply_text, re.DOTALL)
        if videos_match:
            videos_xml = videos_match.group(0)
            text_content = text_content.replace(videos_xml, "").strip()
            
            video_paths = []
            videos_content = videos_match.group(1)
            for vid_match in re.finditer(r'<video\s+path="(.*?)"\s*/>', videos_content):
                video_paths.append(vid_match.group(1))
        
        return cls(text=text_content, poll_data=poll_data, image_paths=image_paths, video_paths=video_paths)
