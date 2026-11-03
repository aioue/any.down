"""Normalize task titles for duplicate detection (case/punctuation insensitive)."""

from __future__ import annotations

import re

_ALEXA_LEAD = re.compile(r"^(reminder from alexa:|alexa reminder:)", re.IGNORECASE)
_SPEAK_PREFIX = re.compile(r"^\U0001f5e3\s*", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_task_title(text: str) -> str:
    s = (text or "").strip()
    s = _ALEXA_LEAD.sub("", s)
    s = _SPEAK_PREFIX.sub("", s)
    return _NON_ALNUM.sub(" ", s.lower()).strip()
