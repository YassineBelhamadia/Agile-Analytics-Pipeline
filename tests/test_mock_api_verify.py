"""mock_api.verify and mock_api.verify_app, inside pytest (step 5.2).

The two scripts stay runnable on their own. These wrappers call their `main`
on the real raw_data/, so `pytest` alone covers them. Neither needs a server
or a database. Each sabotage breaks the code under check, not the data, and
must make `main` return 1 and print FAILED, not raise.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import mock_api.app
from mock_api import verify, verify_app
from mock_api.config import STORY_POINTS_FIELD
from mock_api.serializers import serialize_issue
from test_staging import RAW_DATA


def last_line(capsys) -> str:
    return capsys.readouterr().out.splitlines()[-1]


def test_verify_passes_on_the_ground_truth(capsys) -> None:
    assert verify.main(str(RAW_DATA)) == 0
    assert last_line(capsys) == "ALL CHECKS PASSED"


def test_verify_app_passes_on_the_ground_truth(capsys) -> None:
    assert verify_app.main(str(RAW_DATA)) == 0
    assert last_line(capsys) == "ALL APP CHECKS PASSED"


def test_sabotage_a_shifted_due_date_fails_verify(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """A serializer whose due dates are one day late no longer matches the deadline rule."""
    def one_day_late(issue, index):
        payload = serialize_issue(issue, index)
        due = payload["fields"]["duedate"]
        if due:
            payload["fields"]["duedate"] = (date.fromisoformat(due) + timedelta(days=1)).isoformat()
        return payload

    monkeypatch.setattr(verify, "serialize_issue", one_day_late)
    assert verify.main(str(RAW_DATA)) == 1
    assert last_line(capsys).startswith("FAILED:")


def test_sabotage_a_dropped_field_fails_verify_app(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """An app that drops story points no longer returns what the serializer produces."""
    def without_story_points(issue, index):
        payload = serialize_issue(issue, index)
        del payload["fields"][STORY_POINTS_FIELD]
        return payload

    monkeypatch.setattr(mock_api.app, "serialize_issue", without_story_points)
    assert verify_app.main(str(RAW_DATA)) == 1
    assert last_line(capsys).startswith("FAILED:")
