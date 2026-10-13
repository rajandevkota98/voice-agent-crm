import pytest

from voice_crm.intent import read_intent


@pytest.mark.parametrize(
    "text",
    ["yes please", "Sure, call me", "okay sounds good"],
)
def test_accept_phrases(text):
    assert read_intent(text) == "accept"


@pytest.mark.parametrize(
    "text",
    ["not interested", "No thanks", "please stop calling me", "remove me"],
)
def test_decline_phrases(text):
    assert read_intent(text) == "decline"


def test_not_interested_is_a_decline_not_an_accept():
    assert read_intent("I am not interested") == "decline"


def test_word_boundaries_stop_substring_matches():
    assert read_intent("the bookshop is closed") == "unclear"


def test_empty_or_none_is_unclear():
    assert read_intent("") == "unclear"
    assert read_intent(None) == "unclear"
