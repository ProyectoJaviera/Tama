# Imagen de Tama. Ollama NO va aquí: corre nativo en el host.
# Todo se descarga al construir; en ejecución la imagen no descarga nada.

FROM ghcr.io/astral-sh/uv:0.11.15 AS uv

FROM python:3.12.13-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    HF_HUB_OFFLINE=1

WORKDIR /app

# Dependencias primero (capa cacheable), exactamente las de uv.lock y sin las de desarrollo.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-group bench --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-group bench
# Configuración de los servidores MCP: corren dentro del contenedor, con el mismo Python.
COPY config ./config

RUN useradd --create-home --uid 1000 tama \
    && mkdir -p /data /trabajo /memoria \
    && chown tama:tama /data /trabajo /memoria
USER tama

ENV PATH="/app/.venv/bin:$PATH" \
    TAMA_DATA_DIR=/data \
    TAMA_WORKDIR=/trabajo \
    TAMA_MEMORY_DIR=/memoria
VOLUME ["/data", "/trabajo", "/memoria"]

ENTRYPOINT ["tama"]
