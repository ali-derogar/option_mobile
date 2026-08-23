"""Security-focused API behavior tests."""

from __future__ import annotations

from options.backend.api import main


def test_refresh_status_does_not_expose_export_paths() -> None:
    original_status = dict(main._refresh_status)
    try:
        main._refresh_status.update(
            {
                "running": False,
                "last_error": None,
                "last_result": {
                    "options": 12,
                    "client_type_stats": 8,
                    "money_flow": 8,
                    "open_interest": 12,
                    "exports": {"contracts": "/tmp/private/contracts.csv"},
                },
                "stage": "done",
                "message": "ok",
                "started_at": "2026-08-23T00:00:00+00:00",
                "finished_at": "2026-08-23T00:00:01+00:00",
            }
        )

        status = main._public_refresh_status()

        assert status["last_result"] == {
            "options": 12,
            "client_type_stats": 8,
            "money_flow": 8,
            "open_interest": 12,
        }
    finally:
        main._refresh_status.clear()
        main._refresh_status.update(original_status)
