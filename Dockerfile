FROM python:3.14.6-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app

COPY --chown=app:app . .

USER app

CMD ["python", "--version"]
