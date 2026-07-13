# Local development / demo image. Production deployment config is deferred (D-051).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
# Dev tooling is included so the same image can run pytest against the db service.
COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements-dev.txt

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
