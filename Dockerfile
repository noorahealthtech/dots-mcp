# Optional container image for the remote connector. The primary deploy path is the
# VM (systemd + nginx, see deploy/); this is here for parity/portability.
#
#   docker build -t dots-kms-mcp .
#   docker run --rm -p 8900:8900 --env-file .env \
#     -e KMS_TRANSPORT=streamable-http -e KMS_HOST=0.0.0.0 dots-kms-mcp
#
# Put TLS + a public hostname in front (a reverse proxy / ingress); this image
# speaks plain HTTP on $KMS_PORT.

FROM python:3.12-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first for layer caching (project code excluded).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source (.dockerignore keeps .env and secrets out of the image).
COPY . .
RUN uv sync --frozen --no-dev

# Drop privileges.
RUN useradd --create-home app && chown -R app /app
USER app

ENV KMS_TRANSPORT=streamable-http \
    KMS_HOST=0.0.0.0 \
    KMS_PORT=8900
EXPOSE 8900

CMD ["uv", "run", "--frozen", "dots-kms-mcp"]
