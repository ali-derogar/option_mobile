"""Activation code behavior tests."""

import time

from options.backend import activation


def test_activation_code_round_trip_and_window(monkeypatch) -> None:
    timestamp = 1_787_512_096
    code = activation.encode(timestamp)

    assert activation.decode(code) == timestamp

    monkeypatch.setattr(time, "time", lambda: timestamp + 3599)
    assert activation.is_valid(code)

    monkeypatch.setattr(time, "time", lambda: timestamp + 3601)
    assert not activation.is_valid(code)


def test_activation_code_rejects_invalid_input() -> None:
    assert not activation.is_valid("")
    assert not activation.is_valid("not-a-code")
