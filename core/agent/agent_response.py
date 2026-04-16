from dataclasses import dataclass
from typing import Optional, Dict
import re
import xml.etree.ElementTree as ET

@dataclass
class AgentResponse:
    text: str
    poll_data: Optional[Dict] = None

    @classmethod
    def from_string(cls, reply_text: str) -> 'AgentResponse':
        text_content = reply_text
        poll_data = None
        
        # Find the poll block
        poll_match = re.search(r'<poll(.*?)>(.*?)</poll>', reply_text, re.DOTALL)
        if poll_match:
            poll_xml = poll_match.group(0)
            # Text content is the reply_text without the poll block
            text_content = reply_text.replace(poll_xml, "").strip()
            
            try:
                root = ET.fromstring(poll_xml)
                poll_data = {
                    "question": root.find("question").text or "",
                    "allow_multiple": root.get("allow_multiple") == "true",
                    "options": []
                }
                options_elem = root.find("options")
                if options_elem is not None:
                    for option_elem in options_elem.findall("option"):
                        poll_data["options"].append({
                            "text": option_elem.find("text").text or "",
                            "emoji": option_elem.find("emoji").text or "",
                            "response": option_elem.find("response").text or ""
                        })
            except Exception as e:
                print(f"XML parsing of poll failed, trying manual regex: {e}")
                # Manual fallback for poll
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
        
        return cls(text=text_content, poll_data=poll_data)
