FROM python:3.13.2-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv pip install --system --no-cache .

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

VOLUME ["/app/outputs"]

CMD ["./entrypoint.sh"]
