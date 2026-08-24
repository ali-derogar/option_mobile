"""TSETMC API response parsing tests."""

from options.backend.api_errors import get_data, is_success, parse_api_error


def test_get_data_preserves_falsy_success_payloads() -> None:
    assert get_data({"Data": []}) == []
    assert get_data({"Data": 0}) == 0
    assert get_data({"Data": "", "data": {"fallback": False}}) == ""
    assert get_data({"data": []}) == []


def test_parse_api_error_preserves_zero_code_and_empty_message_field() -> None:
    assert parse_api_error({"Msg": {"Code": 0, "Msg": ""}}) == (0, "خطای نامشخص از سرور")
    assert parse_api_error({"msg": {"code": "12", "msg": "bad"}}) == (12, "bad")
    assert parse_api_error({"msg": {"code": float("inf"), "msg": "bad"}}) == (None, "bad")
    assert parse_api_error({"msg": {"code": "not-a-code", "msg": "bad"}}) == (None, "bad")


def test_is_success_handles_boolean_strings_explicitly() -> None:
    assert is_success({"isSuccess": "true"}) is True
    assert is_success({"IsSuccess": "1"}) is True
    assert is_success({"isSuccess": "false"}) is False
    assert is_success({"IsSuccess": "0"}) is False
