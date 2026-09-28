FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --requirement requirements.lock

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --no-cache-dir --no-deps .

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

FROM base AS test

USER root
COPY requirements-dev.lock ./
RUN pip install --no-cache-dir --requirement requirements-dev.lock
COPY tests ./tests
COPY ops ./ops
USER appuser

CMD ["python", "-m", "pytest", "-q"]

FROM base AS runtime
