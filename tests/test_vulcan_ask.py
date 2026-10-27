"""Tests for the stdlib-only agent client in skills/school-brief."""

from __future__ import annotations

import importlib.util
import io
import json
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

_SCRIPT = Path(__file__).parent.parent / "skills" / "school-brief" / "scripts" / "vulcan-ask"


def _load() -> ModuleType:
    loader = SourceFileLoader("vulcan_ask", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("vulcan_ask", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ask = _load()


def test_shrink_strips_html_and_caps_length() -> None:
    data = {"messages": [{"content": "<p>Dzień&nbsp;dobry</p><p>" + "x" * 50 + "</p>"}]}
    content = ask.shrink(data, 20)["messages"][0]["content"]
    assert "<" not in content
    assert content.startswith("Dzień\xa0dobry")
    assert content.endswith("chars]")


def test_fetch_sends_only_accepted_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VULCAN_API_URL", "http://api.test:8585/")
    seen: list[str] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        seen.append(request.full_url)
        assert request.get_method() == "GET"
        return io.BytesIO(b'{"ok": true}')

    with patch.object(ask.urllib.request, "urlopen", fake_urlopen):
        ask.fetch("exams", {"student": "A B", "days": 7, "since": "7d", "n": None})

    assert seen == ["http://api.test:8585/api/exams?student=A+B&days=7"]


def test_brief_reports_failed_parts_in_place(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_fetch(resource, params):  # type: ignore[no-untyped-def]
        if resource in ("changes", "attendance"):
            raise ask.ApiError("404 from /api/changes")
        return {"resource": resource}

    monkeypatch.setattr(ask, "fetch", fake_fetch)
    assert ask.main(["brief"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["changes"] == {"error": "404 from /api/changes"}
    assert out["exams"] == {"resource": "exams"}
    assert out["grades"] == {"resource": "grades"}


def test_unreachable_api_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_fetch(resource, params):  # type: ignore[no-untyped-def]
        raise ask.ApiError("cannot reach")

    monkeypatch.setattr(ask, "fetch", fake_fetch)
    assert ask.main(["brief"]) == 2
    assert "cannot reach" in capsys.readouterr().err
