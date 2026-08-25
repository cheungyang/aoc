import re
import ast
import json
from typing import Any


def format_tool_response(tool_name: str, payload: str, errors: str = "None") -> str:
    return f"""<{tool_name}_response>
  <payload>{payload}</payload>
  <errors>{errors}</errors>
</{tool_name}_response>"""


def format_error_message(error: Any) -> str:
    """Formats an exception or error payload into a clean, informative error message."""
    if not error:
        return "Sorry, I encountered an error processing the request."

    candidate_errors = []

    def collect_candidates(e):
        if e is None or any(c is e for c in candidate_errors):
            return
        candidate_errors.append(e)
        if isinstance(e, BaseException):
            if e.__cause__:
                collect_candidates(e.__cause__)
            if e.__context__:
                collect_candidates(e.__context__)
            if hasattr(e, "exceptions") and isinstance(e.exceptions, (list, tuple)):
                for sub_e in e.exceptions:
                    collect_candidates(sub_e)
            if hasattr(e, "args"):
                for arg in e.args:
                    if isinstance(arg, BaseException):
                        collect_candidates(arg)

    collect_candidates(error)

    first_meaningful_str = ""

    for err in candidate_errors:
        error_str = str(err).strip() if err is not None else ""
        if error_str == "None":
            error_str = ""

        if not first_meaningful_str and error_str:
            first_meaningful_str = error_str

        code = None
        message = None

        if hasattr(err, "code") and getattr(err, "code") is not None:
            code = getattr(err, "code")
        if hasattr(err, "status_code") and getattr(err, "status_code") is not None:
            code = getattr(err, "status_code")
        if hasattr(err, "http_status") and getattr(err, "http_status") is not None:
            code = getattr(err, "http_status")

        if hasattr(err, "message") and getattr(err, "message") and isinstance(getattr(err, "message"), str):
            message = getattr(err, "message")
        elif hasattr(err, "detail") and getattr(err, "detail") and isinstance(getattr(err, "detail"), str):
            message = getattr(err, "detail")

        # Check response_json / body dicts if present on exception object
        for dict_attr in ("response_json", "body", "error", "data"):
            if hasattr(err, dict_attr):
                val = getattr(err, dict_attr)
                if isinstance(val, dict):
                    err_obj = val.get("error", val)
                    if isinstance(err_obj, dict):
                        if not code:
                            code = err_obj.get("code") or err_obj.get("status_code")
                        if not message:
                            message = err_obj.get("message") or err_obj.get("detail")
                    elif isinstance(err_obj, str) and not message:
                        message = err_obj

        # Try to parse dict/json from error_str
        if error_str:
            dict_match = re.search(r"(\{.*\})", error_str, re.DOTALL)
            if dict_match:
                dict_str = dict_match.group(1)
                parsed_dict = None
                try:
                    parsed_dict = json.loads(dict_str)
                except Exception:
                    try:
                        parsed_dict = ast.literal_eval(dict_str)
                    except Exception:
                        pass

                if isinstance(parsed_dict, dict):
                    err_obj = parsed_dict.get("error", parsed_dict)
                    if isinstance(err_obj, dict):
                        if not code:
                            code = err_obj.get("code") or err_obj.get("status_code")
                        if not message:
                            message = err_obj.get("message") or err_obj.get("detail")
                    elif isinstance(err_obj, str) and not message:
                        message = err_obj
        else:
            dict_match = None

        # Recursively unwrap JSON / dict strings in message
        while isinstance(message, str):
            trimmed = message.strip()
            if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
                try:
                    inner_parsed = json.loads(trimmed)
                except Exception:
                    try:
                        inner_parsed = ast.literal_eval(trimmed)
                    except Exception:
                        break
                if isinstance(inner_parsed, dict):
                    inner_err = inner_parsed.get("error", inner_parsed)
                    if isinstance(inner_err, dict):
                        if not code or str(code) == "None":
                            code = inner_err.get("code") or inner_err.get("status_code") or code
                        message = inner_err.get("message") or inner_err.get("detail") or inner_err.get("error") or message
                    elif isinstance(inner_err, str):
                        message = inner_err
                    else:
                        break
                else:
                    break
            else:
                break

        if not code and error_str:
            code_match = re.search(r"\b([45]\d{2})\b", error_str)
            if code_match:
                code = code_match.group(1)

        if code and message:
            return f"[{code}] {message}"
        elif code and not message:
            cleaned = re.sub(r"^(?:Error code:\s*)?" + re.escape(str(code)) + r"(?:\s+[A-Z_]+(?:\.|\:|\b))?\s*", "", error_str).strip()
            if dict_match and dict_match.group(1) in cleaned:
                cleaned = cleaned.replace(dict_match.group(1), "").strip().rstrip(".-: ")
            if cleaned:
                return f"[{code}] {cleaned}"
            return f"[{code}] Error processing request."
        elif message and message != error_str:
            return message

    if first_meaningful_str:
        return first_meaningful_str

    return "Sorry, I encountered an error processing the request."


def is_service_error(error: Any) -> bool:
    """
    Checks if an exception is a known external service / API error (e.g. 503, 429, 500, ServerError, APIError)
    where full stack traces are unnecessary noise and should be suppressed.
    """
    if not error:
        return False

    err_msg = format_error_message(error)
    # If formatted message starts with an HTTP status code like [503] or [429]
    if re.match(r"^\[(?:4\d\d|5\d\d)\]", err_msg):
        return True

    candidate_errors = []

    def collect_candidates(e):
        if e is None or any(c is e for c in candidate_errors):
            return
        candidate_errors.append(e)
        if isinstance(e, BaseException):
            if e.__cause__:
                collect_candidates(e.__cause__)
            if e.__context__:
                collect_candidates(e.__context__)
            if hasattr(e, "exceptions") and isinstance(e.exceptions, (list, tuple)):
                for sub_e in e.exceptions:
                    collect_candidates(sub_e)
            if hasattr(e, "args"):
                for arg in e.args:
                    if isinstance(arg, BaseException):
                        collect_candidates(arg)

    collect_candidates(error)

    known_error_types = (
        "ServerError", "ClientError", "APIError", "RateLimitError",
        "ServiceUnavailable", "ResourceExhausted", "InternalServerError",
        "HTTPStatusError", "HTTPException", "GoogleAPICallError",
        "APIConnectionError", "APITimeoutError", "BadGateway", "GatewayTimeout"
    )

    for err in candidate_errors:
        err_type = type(err).__name__
        err_mod = getattr(type(err), "__module__", "")

        if any(name in err_type for name in known_error_types):
            return True

        if any(api_mod in err_mod for api_mod in ("genai", "google", "openai", "anthropic", "httpx", "aiohttp", "urllib3")):
            return True

        for attr in ("status_code", "code", "http_status"):
            if hasattr(err, attr):
                val = getattr(err, attr)
                if isinstance(val, int) and (400 <= val < 600):
                    return True
                if isinstance(val, str) and val.isdigit() and (400 <= int(val) < 600):
                    return True

        err_str = str(err)
        if any(marker in err_str for marker in (
            "503 Service Unavailable", "503 UNAVAILABLE", "429 RESOURCE_EXHAUSTED",
            "429 Too Many Requests", "Quota exceeded", "high demand",
            "Resource has been exhausted", "Model is overloaded"
        )):
            return True

    return False
