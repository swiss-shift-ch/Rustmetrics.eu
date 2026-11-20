import secrets

from rm_token import CALENDAR_TOKEN_MAX_LEN, is_plausible_calendar_token


def test_accepts_generated_token():
    assert is_plausible_calendar_token(secrets.token_urlsafe(32))


def test_rejects_nul_byte():
    assert not is_plausible_calendar_token("abcdefghijklmnop\x00qrs")


def test_rejects_foreign_characters():
    assert not is_plausible_calendar_token("abcdefghijklmnop qrs")
    assert not is_plausible_calendar_token("abcdefghijklmnop'qrs")
    assert not is_plausible_calendar_token("abcdefghijklmnopä")


def test_rejects_too_short_and_too_long():
    assert not is_plausible_calendar_token("short")
    assert not is_plausible_calendar_token("a" * (CALENDAR_TOKEN_MAX_LEN + 1))


def test_rejects_none_and_empty():
    assert not is_plausible_calendar_token(None)
    assert not is_plausible_calendar_token("")


def test_rejects_trailing_newline():
    assert not is_plausible_calendar_token(secrets.token_urlsafe(32) + "\n")
