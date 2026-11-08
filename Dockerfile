FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY agent_runtime ./agent_runtime
COPY agents ./agents
COPY evals/cases ./evals/cases
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home agent

USER 10001
EXPOSE 8080
CMD ["python", "-m", "agent_runtime", "serve"]
