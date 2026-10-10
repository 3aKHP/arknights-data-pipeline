"""Normalize legacy Dialog/PopupDialog inline narration into name lines.

Older roguelike monthly-chat scripts (RO1 era) carry dialogue as
`[Dialog(head="char_405_absin", delay=1)]台词` (or `[PopupDialog(...)]`),
sometimes with the text on the following lines instead. ASTR's
jsonconvert keeps the head/delay attributes but drops the text, and
consumers do not render Dialog props — so those chapters would convert
to empty shells.

This module rewrites such commands into ordinary `[name="..."]text`
lines before ASTR sees them: speaker ids are resolved through
character_table (unresolvable ids degrade to anonymous "？？？", never to
a guessed name). Bare `[Dialog]` staging commands (no inline or trailing
text) pass through untouched.
"""

from __future__ import annotations

import re
from typing import Callable

#: [Dialog(...)] / [PopupDialog(...)] header, attributes, and any
#: same-line trailing text
_DIALOG_RE = re.compile(
    r"^\[(?P<prop>Dialog|PopupDialog)\((?P<attrs>[^)]*)\)\](?P<rest>.*)$"
)
_HEAD_RE = re.compile(r'\bhead\s*=\s*"(?P<head>[^"]+)"')

ANONYMOUS = "？？？"


def _speaker(attrs: str, resolve_speaker: Callable[[str], str | None]) -> str:
    m = _HEAD_RE.search(attrs)
    name = resolve_speaker(m.group("head")) if m else None
    return name or ANONYMOUS


def normalize_dialog_text(text: str,
                          resolve_speaker: Callable[[str], str | None]) -> str:
    """Rewrite text-carrying Dialog/PopupDialog commands as name lines.

    `resolve_speaker(head_id) -> str | None` maps a head attribute value
    (e.g. "char_405_absin") to a display name; None means anonymous.
    Returns the input unchanged when nothing matches.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        m = _DIALOG_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        rest = m.group("rest").strip()
        if rest:
            out.append(f'[name="{_speaker(m.group("attrs"), resolve_speaker)}"]{rest}')
            changed = True
            i += 1
            continue
        # no same-line text: gather trailing text lines up to the next command
        j = i + 1
        tail: list[str] = []
        while j < len(lines) and not lines[j].lstrip().startswith("["):
            if lines[j].strip():
                tail.append(lines[j].strip())
            j += 1
        if tail:
            out.append(
                f'[name="{_speaker(m.group("attrs"), resolve_speaker)}"]' + "\n".join(tail)
            )
            changed = True
            i = j
        else:
            out.append(line)  # bare staging command
            i += 1
    return "\n".join(out) if changed else text


def speaker_resolver(character_table: dict) -> Callable[[str], str | None]:
    """Build a resolve_speaker callable from character_table.json content."""
    names = {
        cid: (data.get("name") or None)
        for cid, data in character_table.items()
        if isinstance(data, dict)
    }
    return names.get
