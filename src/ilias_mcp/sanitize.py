"""Output sanitising for untrusted LMS text (SECURITY_BASELINE SEC-12/SEC-13/SEC-14).

Every string that reaches the model passes through :func:`clean_text`:

* ANSI/VT escape sequences are removed,
* control characters except ``\\n`` and ``\\t`` are removed,
* zero-width and bidi override characters are removed,
* markdown images and links are rendered as inert plain text
  (``[Bild: alt] (url)`` / ``text (url)``) so a client never auto-loads an URL,
* the result is truncated to ``limit`` characters.

:func:`redact` additionally replaces known secret values (session cookies,
Moodle token) with ``[REDACTED]`` as a last line of defence (CT-1).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

DEFAULT_LIMIT = 2000
TRUNCATION_MARK = " …[gekürzt]"
REDACTED = "[REDACTED]"
#: Secrets shorter than this are not redacted (too many false positives, e.g. "1").
MIN_SECRET_LEN = 8

_ANSI_RE = re.compile(
    r"(?:\x1b\[[0-?]*[ -/]*[@-~])"  # CSI
    r"|(?:\x1b\][^\x07\x1b]*(?:\x07|\x1b\\))"  # OSC
    r"|(?:\x1b[@-Z\\-_])"  # 2-char escapes
    r"|(?:\x9b[0-?]*[ -/]*[@-~])"  # 8-bit CSI
)
_INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad\u180e]"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)")
#: Query parameters that may carry credentials; their values are always dropped.
_SECRET_QUERY_RE = re.compile(r"(?i)\b(wstoken|token|sesskey|phpsessid|il_wac_token)=[^&\s#\"']*")


def _defang_markdown(text: str) -> str:
    text = _MD_IMAGE_RE.sub(lambda m: f"[Bild: {m.group(1)}] ({m.group(2)})", text)
    return _MD_LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)


def clean_text(value: str, limit: int = DEFAULT_LIMIT) -> tuple[str, bool]:
    """Return ``(clean, truncated)`` for one untrusted string."""
    text = _ANSI_RE.sub("", value)
    text = _INVISIBLE_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    text = _defang_markdown(text)
    text = _SECRET_QUERY_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    if len(text) > limit:
        return text[: max(0, limit - len(TRUNCATION_MARK))] + TRUNCATION_MARK, True
    return text, False


def clean_str(value: str, limit: int = DEFAULT_LIMIT) -> str:
    """Like :func:`clean_text` but only returns the cleaned string."""
    return clean_text(value, limit)[0]


def clean_tree(obj: Any, limit: int = DEFAULT_LIMIT) -> tuple[Any, bool]:
    """Recursively sanitise all strings (values *and* keys) in a JSON-like tree."""
    if isinstance(obj, str):
        return clean_text(obj, limit)
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        truncated = False
        for key, val in obj.items():
            ckey, kt = clean_text(str(key), 200)
            cval, vt = clean_tree(val, limit)
            out[ckey] = cval
            truncated = truncated or kt or vt
        return out, truncated
    if isinstance(obj, (list, tuple)):
        items = []
        truncated = False
        for val in obj:
            cval, vt = clean_tree(val, limit)
            items.append(cval)
            truncated = truncated or vt
        return items, truncated
    return obj, False


def redact(obj: Any, secrets: Iterable[str]) -> Any:
    """Replace every occurrence of a known secret value in a JSON-like tree."""
    values = sorted({s for s in secrets if isinstance(s, str) and len(s) >= MIN_SECRET_LEN},
                    key=len, reverse=True)
    if not values:
        return obj

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            for secret in values:
                if secret in node:
                    node = node.replace(secret, REDACTED)
            return node
        if isinstance(node, dict):
            return {_walk(str(k)): _walk(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [_walk(v) for v in node]
        return node

    return _walk(obj)
