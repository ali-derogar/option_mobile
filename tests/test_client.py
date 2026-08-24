"""TSETMC client edge-case tests."""

import pytest
import requests

from options.backend.client import TsetmcAPIError, TsetmcClient


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_parse_json_rejects_non_object_payload() -> None:
    client = TsetmcClient(username="u", password="p")

    with pytest.raises(TsetmcAPIError, match="JSON object expected"):
        client._parse_json(FakeResponse([]))


def test_login_rejects_success_payload_with_non_object_data(monkeypatch) -> None:
    client = TsetmcClient(username="u", password="p", max_retries=1)

    def fake_post(*args, **kwargs):
        return FakeResponse({"IsSuccess": True, "Data": []})

    monkeypatch.setattr(client._session, "post", fake_post)

    with pytest.raises(TsetmcAPIError, match="response data is invalid"):
        client.login(force=True)


def test_login_rejects_success_payload_with_non_string_token(monkeypatch) -> None:
    client = TsetmcClient(username="u", password="p", max_retries=1)

    def fake_post(*args, **kwargs):
        return FakeResponse({"IsSuccess": True, "Data": {"Token": ["bad"]}})

    monkeypatch.setattr(client._session, "post", fake_post)

    with pytest.raises(TsetmcAPIError, match="no token"):
        client.login(force=True)


def test_login_tolerates_non_string_expiry(monkeypatch) -> None:
    client = TsetmcClient(username="u", password="p", max_retries=1)

    def fake_post(*args, **kwargs):
        return FakeResponse({"IsSuccess": True, "Data": {"Token": "  abc  ", "ExpireDate": 123}})

    monkeypatch.setattr(client._session, "post", fake_post)

    assert client.login(force=True) == "abc"
    assert client._token_expires is not None


def test_change_password_wraps_timeout(monkeypatch) -> None:
    client = TsetmcClient(username="u", password="p")

    def fake_post(*args, **kwargs):
        raise requests.Timeout("slow")

    monkeypatch.setattr(client._session, "post", fake_post)

    with pytest.raises(TsetmcAPIError, match="تغییر رمز"):
        client.change_password("new")


def test_change_password_wraps_request_errors(monkeypatch) -> None:
    client = TsetmcClient(username="u", password="p")

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(client._session, "post", fake_post)

    with pytest.raises(TsetmcAPIError, match="خطای ارتباط"):
        client.change_password("new")
