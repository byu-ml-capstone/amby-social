FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1 PATH=/srv/.venv/bin:$PATH
WORKDIR /srv

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
