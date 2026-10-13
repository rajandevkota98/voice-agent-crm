"""Turns a short reply, typed or spoken, into one of three intents.

This is keyword matching on purpose. It is easy to read and easy to test, and it
can be swapped for an LLM classifier later without touching the callers.
"""

import re

DECLINE_PHRASES = ("not interested", "no thanks", "stop", "do not call", "don't call", "remove me")
ACCEPT_PHRASES = ("yes", "yeah", "sure", "interested", "sounds good", "call me", "okay")

STATUS_FOR_INTENT = {"accept": "interested", "decline": "not_interested"}


def _mentions(text, phrases):
    return any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in phrases)


def read_intent(text):
    lowered = (text or "").lower().strip()
    if _mentions(lowered, DECLINE_PHRASES):
        return "decline"
    if _mentions(lowered, ACCEPT_PHRASES):
        return "accept"
    return "unclear"
