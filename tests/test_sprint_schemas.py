from datetime import date

import pytest
from pydantic import ValidationError

from app.models import SprintStatus, TicketStatus
from app.schemas import SprintClose, SprintCreate, SprintHistoryOut, SprintOut, SprintUpdate


def test_sprint_create_is_strict_count_only():
    assert SprintCreate().count == 1
    assert SprintCreate(count=5).count == 5
    for value in (0, 6, 1.0, "1"):
        with pytest.raises(ValidationError):
            SprintCreate(count=value)
    with pytest.raises(ValidationError):
        SprintCreate(name="Legacy")


def test_sprint_update_is_goal_or_activation_only_and_allows_blank_goal():
    assert SprintUpdate(goal="  Ship billing ").goal == "Ship billing"
    assert SprintUpdate(goal="   ").goal == ""
    assert SprintUpdate(status=SprintStatus.ACTIVE).status is SprintStatus.ACTIVE
    with pytest.raises(ValidationError):
        SprintUpdate(name="Renamed")
    with pytest.raises(ValidationError):
        SprintUpdate(status=SprintStatus.CLOSED)


def test_sprint_close_is_empty_and_forbids_legacy_destination():
    assert SprintClose().model_dump() == {}
    with pytest.raises(ValidationError):
        SprintClose(next_sprint_id="next-sprint")


def test_sprint_out_exposes_public_sprint_fields():
    sprint = SprintOut(
        id="sprint-1",
        project_id="project-1",
        name="Sprint 1",
        goal="",
        status=SprintStatus.PLANNING,
        start_date=date(2026, 9, 21),
        end_date=date(2026, 10, 4),
        committed_points=None,
        completed_points=None,
        closed_at=None,
    )
    assert sprint.id == "sprint-1"
    assert sprint.status is SprintStatus.PLANNING


def test_sprint_history_out_validates_and_serializes_joined_projection():
    history = SprintHistoryOut.model_validate(
        {
            "ticket_id": "ticket-1",
            "ticket_number": 7,
            "title": "Checkout",
            "status_at_close": "IN_PROGRESS",
            "story_points_at_close": 3,
            "was_completed": False,
        }
    )
    assert history.model_dump(mode="json") == {
        "ticket_id": "ticket-1",
        "ticket_number": 7,
        "title": "Checkout",
        "status_at_close": TicketStatus.IN_PROGRESS.value,
        "story_points_at_close": 3,
        "was_completed": False,
    }
