import time

import pytest
from sqlmodel import Session, select

from app.models import Project, Ticket, TicketStatus, TicketType, User
from app.routers.web import BOARD_TICKETS_PER_COLUMN

TICKET_COUNT = 5000
QUERY_RUNS = 50


@pytest.mark.benchmark
def test_board_query_p95_under_20ms(engine, capsys):
    with Session(engine) as session:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        project = Project(name="Payment Gateway", slug="payment-gateway", key="PAY")
        session.add(user)
        session.add(project)
        session.commit()
        session.refresh(user)
        session.refresh(project)
        statuses = list(TicketStatus)
        session.add_all(
            [
                Ticket(
                    ticket_number=n,
                    project_id=project.id,
                    title=f"Ticket {n}",
                    description="x" * 200,
                    type=TicketType.TASK,
                    status=statuses[n % len(statuses)],
                    creator_id=user.id,
                )
                for n in range(1, TICKET_COUNT + 1)
            ]
        )
        session.commit()
        project_id = project.id

    durations = []
    with Session(engine) as session:
        for _ in range(QUERY_RUNS):
            started = time.perf_counter()
            rows = [
                session.exec(
                    select(Ticket)
                    .where(Ticket.project_id == project_id, Ticket.status == ticket_status)
                    .order_by(Ticket.ticket_number.desc())
                    .limit(BOARD_TICKETS_PER_COLUMN + 1)
                ).all()
                for ticket_status in TicketStatus
            ]
            durations.append(time.perf_counter() - started)
            assert all(len(column) == BOARD_TICKETS_PER_COLUMN + 1 for column in rows)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    with capsys.disabled():
        print(
            f"\nV-2 capped board queries over {TICKET_COUNT} tickets, {QUERY_RUNS} runs: "
            f"min={durations[0] * 1000:.2f} ms "
            f"median={durations[len(durations) // 2] * 1000:.2f} ms "
            f"p95={p95 * 1000:.2f} ms"
        )
    assert p95 < 0.020, f"p95 was {p95 * 1000:.2f} ms, budget is 20 ms"
