"""Tests for reminder key normalization."""

from anydown.reminder_keys import normalize_reminder_key


def test_normalize_strips_speak_prefix_and_punctuation() -> None:
    assert normalize_reminder_key("\U0001f5e3 Kitchen beeping!") == "kitchen beeping"


def test_normalize_alexa_lead() -> None:
    assert normalize_reminder_key("Reminder from Alexa: kitchen beeping") == "kitchen beeping"
