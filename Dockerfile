FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY agent_runtime ./agent_runtime
COPY agents ./agents
COPY smoke ./smoke
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock && /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY agents ./agents
COPY smoke ./smoke
RUN useradd --uid 10001 --create-home agent

USER 10001
EXPOSE 8088
CMD ["python", "-m", "portfolio_agent_factory", "serve"]
