"""Static checks for frontend-only security hardening."""

from __future__ import annotations

from pathlib import Path


APP_JS = Path("src/options/web/static/js/app.js")


def test_dynamic_ui_classes_are_whitelisted() -> None:
    source = APP_JS.read_text(encoding="utf-8")

    assert "function trendStateClass" in source
    assert "trendStateClass(summary?.class_name)" in source
    assert "trendStateClass(person.class_name)" in source
    assert "function toastTypeClass" in source
    assert "toast ${toastTypeClass(type)}" in source
    assert 'summary?.class_name || "neutral"' not in source
    assert 'person.class_name || "neutral"' not in source
    assert "toast ${type}" not in source


def test_csv_export_ui_does_not_return() -> None:
    source = APP_JS.read_text(encoding="utf-8")

    assert "exportCsv" not in source
    assert "btnExport" not in source
    assert "text/csv" not in source
