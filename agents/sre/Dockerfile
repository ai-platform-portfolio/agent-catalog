FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS build

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1
WORKDIR /app

COPY requirements.lock ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock

COPY runtime.py server.py tools.py agent.yaml evaluate.py ./
COPY tests ./tests
COPY evals ./evals
RUN /opt/venv/bin/python -m unittest discover -s tests -v

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY runtime.py server.py tools.py agent.yaml ./
RUN useradd --uid 10001 --create-home agent

USER 10001
EXPOSE 8088
CMD ["python", "/app/server.py"]
