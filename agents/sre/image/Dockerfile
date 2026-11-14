FROM python:3.12-alpine3.24@sha256:c5ba3deef00c7758561a0f03c26f44f10589263d40e732b8748e0d1f38f6dd89 AS base

# Fixed upstream Kerberos package; refresh this pin with the base image.
RUN apk add --no-cache --upgrade krb5-libs=1.22.3-r0

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PATH=/opt/venv/bin:$PATH
WORKDIR /app

FROM base AS build

COPY image/requirements.txt ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt

COPY config ./config
COPY runtime ./runtime
COPY hosting ./hosting
COPY tools ./tools
COPY evaluation ./evaluation
COPY tests ./tests
RUN /opt/venv/bin/python -m unittest discover -s tests -v


FROM base AS runtime

COPY --from=build /opt/venv /opt/venv
COPY config ./config
COPY runtime ./runtime
COPY hosting ./hosting
COPY tools ./tools
RUN addgroup -g 10001 agent && adduser -D -u 10001 -G agent agent && chmod 700 /home/agent

USER 10001:10001
EXPOSE 8088
CMD ["python", "-m", "hosting.server"]
