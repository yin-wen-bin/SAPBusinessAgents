"""SDK-free authentication classification. Never return messages or credentials."""
import json
import re

UNAUTHORIZED = "workbuddy_authentication_required"
REFRESH_FAILED = "workbuddy_authentication_refresh_failed"
AUTH_MARKER = re.compile(r"authentication|unauthori[sz]ed|auth[_ -]?error|token[_ -]?refresh|refresh[_ -]?token", re.I)
HTTP_401 = re.compile(r"(?:HTTP(?:/[12](?:\.\d)?)?\s*[:=]?\s*401\b|(?:status(?:_code|Code)?|code)\s*[\"']?\s*[:=]\s*[\"']?401\b)", re.I)
LOGIN_REQUIRED = re.compile(r"^(?:(?:AuthenticationError|UnauthorizedError|e):\s*)?Authentication required\.\s+Please use /login command to sign in to your account[.!]?$", re.I)


def authentication_failure(value, *, authentication_scope=False):
    if isinstance(value, dict):
        # Authentication control replies are already scoped; generic business
        # responses are not. A SAP 401 must not expire the WorkBuddy account.
        status = value.get("status_code", value.get("statusCode", value.get("http_status")))
        if authentication_scope and type(status) is int and status == 401:
            return UNAUTHORIZED
        text = json.dumps({k: value[k] for k in ("error", "message", "type", "code", "status_code", "statusCode") if k in value}, ensure_ascii=True)
    elif isinstance(value, str):
        text = value
    else:
        return None
    if len(text) > 32768:
        return None
    if not authentication_scope and re.search(r'\bSAP\b|\bOData\b|sap_query_|tool_result', text, re.I):
        return None
    # An exact CLI error is sufficient; never invent HTTP 401 for this case.
    message = value.get('message', value.get('error')) if isinstance(value, dict) else value
    if isinstance(message, str) and LOGIN_REQUIRED.fullmatch(message.strip()):
        return UNAUTHORIZED
    if HTTP_401.search(text) and (authentication_scope or AUTH_MARKER.search(text)):
        return UNAUTHORIZED
    if re.search(r"\bTokenRefreshError\b|\btoken_refresh_failed\b", text):
        return REFRESH_FAILED
    return None


class StderrAuthenticationSignal:
    """Bounded line framing; no raw bytes survive beyond the current line."""
    def __init__(self):
        self.pending = b""
        self.discarding = False

    def feed(self, chunk):
        for line in (self.pending + chunk).splitlines(keepends=True):
            complete = line.endswith((b"\n", b"\r"))
            if self.discarding or len(line) > 32768:
                self.discarding = not complete
                self.pending = b""
                continue
            if not complete:
                self.pending = line
                continue
            self.pending = b""
            text = line.decode('utf-8', errors='replace')
            # Only an SDK/auth error record, not arbitrary stdout, a JSON row,
            # a quoted model message, or a tool's business response.
            if re.match(r"^(?:\[[^\]\r\n]{1,80}\]\s*)?(?:ERROR\s+(?:\[?(?:CodeBuddy|SDK|Auth)\]?\s+)|AuthenticationError\b|TokenRefreshError\b|UnauthorizedError\b)", text, re.I):
                code = authentication_failure(text)
                if code:
                    return {"failure_code": code, "failure_source": "owned_worker_stderr",
                            **({"http_status": 401} if code == UNAUTHORIZED and HTTP_401.search(text) else {}),
                            **({'authentication_reason':'login_required'} if LOGIN_REQUIRED.fullmatch(text.strip()) else {})}
        return None
