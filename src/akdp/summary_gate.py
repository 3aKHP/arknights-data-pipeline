"""Acceptance gates for LLM-generated story summaries.

Two consumers depend on this module and must never drift apart:

- ``summarize``: per-call validation that drives in-process retries and the
  ``failed_details`` failure channel.
- ``validate``: the full-inventory release gate over the shipped
  ``summaries.json`` / ``event_summaries.json``.

All thresholds and tables live here as constants; env-based overrides are
deliberately not wired up yet (add them only when a real need appears).
"""

from __future__ import annotations

import json
import re

CHAPTER = "chapter"
EVENT = "event"

#: minimum accepted length in characters, per summary level
MIN_LENGTH = {CHAPTER: 50, EVENT: 120}

#: chapters without extractable dialogue get this fixed placeholder instead
#: of an LLM call; it is exempt from the length floor
SENTINEL_NO_DIALOGUE = "（无对话内容）"

#: finish_reason values from OpenAI-compatible Chat Completions endpoints.
#: stop = natural completion; the retry set covers documented transient
#: interruptions (DeepSeek insufficient_system_resource, GLM network_error,
#: OpenRouter error). Everything else — including a missing value — fails:
#: the field is required by the spec, and length/content_filter/sensitive/
#: tool_calls are terminal for our single-shot summarization use case.
FINISH_REASON_ACCEPT = frozenset({"stop"})
FINISH_REASON_RETRY = frozenset({"insufficient_system_resource", "network_error", "error"})

#: a summary whose last character is a CJK ideograph, letter, digit, or a
#: "continuation" punctuation was almost surely cut mid-sentence
_CONTINUATION_PUNCT = ",，;；、：:—-～~（(「『《<“\"'"
_TERMINAL_BAD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
_ECHO_PREFIX_RE = re.compile(r"^\s*(摘要|梗概)\s*[：:]")


def classify_finish_reason(reason: str | None) -> str:
    """Classify a finish_reason value as accept / retry / fail."""
    if reason in FINISH_REASON_ACCEPT:
        return "accept"
    if reason in FINISH_REASON_RETRY:
        return "retry"
    return "fail"


def _terminal_bad(ch: str) -> bool:
    return ch in _CONTINUATION_PUNCT or bool(_TERMINAL_BAD_RE.match(ch))


def accept_summary(level: str, text: str | None) -> tuple[bool, str | None]:
    """Return (ok, reject_reason) for a summary at the given level.

    Reject reasons: empty, format_pollution, too_short, truncated_terminal.
    The no-dialogue sentinel is always accepted.
    """
    if text is None:
        return False, "empty"
    s = text.strip()
    if not s:
        return False, "empty"
    if s == SENTINEL_NO_DIALOGUE:
        return True, None
    if _ECHO_PREFIX_RE.match(s) or "```" in s:
        return False, "format_pollution"
    if s[0] in "{[":
        try:
            json.loads(s)
        except json.JSONDecodeError:
            pass
        else:
            return False, "format_pollution"
    if len(s) < MIN_LENGTH.get(level, MIN_LENGTH[CHAPTER]):
        return False, "too_short"
    if _terminal_bad(s[-1]):
        return False, "truncated_terminal"
    return True, None
