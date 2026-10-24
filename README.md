# Tama

Tama es un asistente de IA personal y acompañante diario: conversa, recuerda, opina y sugiere.
Funciona **100% sin internet**; la nube solo mejora la calidad.

Estado: Sprint 0 (fundaciones). Por ahora hay una CLI mínima que conversa con un modelo local de
Ollama y un benchmark para elegir ese modelo. Ver `docs/sprints/` y `CHANGELOG.md`.

## Arquitectura de instalación

- **Ollama corre nativo en el host** (Windows, Linux o macOS), fuera de Docker, para usar todo
  el hardware sin la capa de virtualización.
- **Tama corre en Docker Compose** (un solo servicio, `tama`) y habla con Ollama por la variable
  `OLLAMA_HOST`, que en Compose apunta a `http://host.docker.internal:11434`.
- Al iniciar, Tama comprueba que Ollama responda y que el modelo esté descargado; si no, lo dice
  con un mensaje claro en vez de fallar.

## Requisitos

- [Ollama](https://ollama.com/download) **0.40.5** (versión probada).
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (en Windows usa WSL2), o
  Docker Engine + Compose en Linux.
- 16 GB de RAM como mínimo; 32 GB recomendados para el modelo de 8B. No hace falta GPU.
- Para desarrollar sin Docker: [uv](https://docs.astral.sh/uv/) (instala Python 3.12 solo).

## Instalación (una sola vez, con internet)

Todo lo que se descarga se descarga aquí; Tama nunca descarga nada al arrancar.

```powershell
# 1. Ollama nativo + modelos (Q4). Ver docs/adr/0001-modelo-local.md
ollama pull qwen3:4b      # modelo principal
ollama pull qwen3:1.7b    # tareas de fondo

# 2. Tama
git clone https://github.com/ProyectoJaviera/Tama.git
cd Tama
copy .env.example .env    # ajusta los valores si hace falta
docker compose build
```

## Uso

```powershell
docker compose run --rm tama "hola"        # un mensaje, respuesta en streaming
docker compose run --rm tama               # chat interactivo; 'salir' para terminar
docker compose up -d; docker attach tama   # Tama siempre encendido (se reinicia solo)
docker compose run --rm tama --check       # chequeo de salud
```

Ollama debe estar encendido en el host (la app de Ollama se inicia con Windows; si no,
`ollama serve`). No hace falta exponer Ollama en la red: el contenedor lo alcanza aunque Ollama
escuche solo en `127.0.0.1`.

## Instalación sin internet (en otra máquina)

En una máquina con internet:

```powershell
docker compose build
docker save tama:0.1.0 -o tama-0.1.0.tar
# Copiar también: el instalador de Ollama 0.40.5 y la carpeta de modelos
#   %USERPROFILE%\.ollama\models   (en Linux: ~/.ollama/models o /usr/share/ollama/.ollama/models)
```

En la máquina sin internet:

```powershell
# 1. Instalar Ollama con el instalador copiado
# 2. Copiar la carpeta de modelos a %USERPROFILE%\.ollama\models y reiniciar Ollama
ollama list                         # deben aparecer qwen3:4b y qwen3:1.7b
# 3. Cargar la imagen y arrancar (desde una copia del repo)
docker load -i tama-0.1.0.tar
docker compose up -d --no-build
```

## Configuración

Variables en `.env` (ver `.env.example`):

| Variable | Por defecto | Qué hace |
|---|---|---|
| `OLLAMA_HOST` | Compose: `http://host.docker.internal:11434` | Dirección de Ollama |
| `TAMA_OLLAMA_HOST` | `http://127.0.0.1:11434` | Igual, pero con prioridad (fuera de Docker) |
| `TAMA_LOCAL_MODEL` | `qwen3:4b` | Modelo local |
| `TAMA_THINK` | `false` | Razonamiento del modelo (lento en CPU) |
| `TAMA_TIMEOUT` | `300` | Segundos máximos por respuesta |
| `TAMA_DATA_DIR` | Docker: `/data` (volumen `tama-data`) | Datos de Tama (memoria, bases) |

Los secretos (API keys) van solo en `.env`, que nunca se versiona.

## Desarrollo (sin Docker)

```powershell
uv sync
uv run tama "hola"
uv run pytest            # tests (LLM simulado, no necesitan Ollama)
uv run ruff check .      # lint
uv run ruff format .     # formato
uv run python bench/models.py --write qwen3:1.7b qwen3:4b qwen3:8b   # benchmark
```

Si `uv sync` falla con `invalid peer certificate: UnknownIssuer` (antivirus o proxy que
inspecciona TLS, como Avast), usa los certificados del sistema: `uv sync --system-certs`
(o la variable `UV_SYSTEM_CERTS=1`).

Reglas del proyecto y forma de trabajo: `CLAUDE.md`.
