"""Tests for task title normalization."""

from anydown.task_title import normalize_task_title


def test_normalize_strips_speak_prefix_and_punctuation() -> None:
    assert normalize_task_title("\U0001f5e3 Kitchen beeping!") == "kitchen beeping"


def test_normalize_alexa_lead() -> None:
    assert normalize_task_title("Reminder from Alexa: kitchen beeping") == "kitchen beeping"
