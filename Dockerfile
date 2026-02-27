FROM python:3.13.2-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install supercronic (multi-arch)
ARG TARGETARCH
ENV SUPERCRONIC_VERSION=v0.2.43
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) \
            SUPERCRONIC_URL="https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64"; \
            SUPERCRONIC_SHA1SUM="f97b92132b61a8f827c3faf67106dc0e4467ccf2"; \
            ;; \
        arm64) \
            SUPERCRONIC_URL="https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-arm64"; \
            SUPERCRONIC_SHA1SUM="5c6266786c2813d6f8a99965d84452faae42b483"; \
            ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
    esac; \
    curl -fsSLO "$SUPERCRONIC_URL" \
    && echo "${SUPERCRONIC_SHA1SUM}  supercronic-linux-${TARGETARCH}" | sha1sum -c - \
    && chmod +x "supercronic-linux-${TARGETARCH}" \
    && mv "supercronic-linux-${TARGETARCH}" /usr/local/bin/supercronic

COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv pip install --system --no-cache .

COPY entrypoint.sh crontab ./
RUN chmod +x entrypoint.sh

VOLUME ["/app/outputs"]

CMD ["supercronic", "/app/crontab"]
