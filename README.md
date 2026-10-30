# Tama

Tama es un asistente de IA personal y acompañante diario: conversa, recuerda, opina y sugiere.
Funciona **100% sin internet**; la nube solo mejora la calidad.

Estado: Sprint 1 (conversación). Tama conversa por CLI con un cerebro en cascada que sigue
respondiendo sin internet. Ver `docs/sprints/` y `CHANGELOG.md`.

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
- 16 GB de RAM como mínimo; 32 GB recomendados. No usa GPU: todo corre en CPU y RAM.

### Ollama solo con CPU

Tama pide `num_gpu: 0` en cada mensaje, pero Ollama puede usar una GPU débil por Vulkan para
otros programas. Para bloquearla del todo, define estas variables de usuario y reinicia Ollama:

```powershell
setx GGML_VK_VISIBLE_DEVICES -1
setx OLLAMA_VULKAN 0
setx CUDA_VISIBLE_DEVICES -1
setx OLLAMA_MAX_LOADED_MODELS 1
setx OLLAMA_NUM_PARALLEL 1
```

Comprueba con `ollama ps` que la columna PROCESSOR diga `100% CPU`.
- Para desarrollar sin Docker: [uv](https://docs.astral.sh/uv/) (instala Python 3.12 solo).

## Instalación (una sola vez, con internet)

Todo lo que se descarga se descarga aquí; Tama nunca descarga nada al arrancar.

```powershell
# 1. Ollama nativo + modelos (Q4). Ver docs/adr/0001-modelo-local.md
ollama pull qwen3:4b-instruct-2507-q4_K_M   # modelo principal
ollama pull qwen3:4b                      # tareas de fondo

# 2. Tama
git clone https://github.com/ProyectoJaviera/Tama.git
cd Tama
copy .env.example .env    # ajusta los valores si hace falta
docker compose build
```

## Uso

```powershell
docker compose run --rm tama "hola"        # un mensaje, respuesta en streaming
docker compose run --rm tama               # chat; /proveedor muestra las capas, /salir termina
docker compose up -d; docker attach tama   # Tama siempre encendido (se reinicia solo)
docker compose run --rm tama --check       # chequeo de salud
```

Debajo de cada respuesta aparece en gris qué capa respondió y cuánto tardó. Cada turno queda
registrado en `events.db` (en el volumen de datos).

### Cerebro en cascada

Tama prueba tres capas en orden y usa la primera que responda:

1. **Nube de Ollama** (`gemma4:31b-cloud`), si hay internet y el Ollama del PC tiene la sesión
   iniciada (`ollama signin`); no necesita clave. Con `OLLAMA_API_KEY` va directo a ollama.com.
2. **Claude** (`claude-haiku-5-5`), si hay `ANTHROPIC_API_KEY`, internet y no se superó el tope
   mensual (`TAMA_CLAUDE_MONTHLY_USD`, US$ 5 por defecto).
3. **Ollama local**, siempre disponible y sin internet.

Si una capa no da el primer token en 8 s o falla a mitad de la respuesta, el turno se repite
completo en la siguiente y Tama lo avisa ("seguí en local"). Tres fallos seguidos dejan una capa
fuera por 5 minutos. Sin internet, Tama va directo a la capa local sin esperar.
Sin claves y sin sesión de Ollama, todo funciona igual con la capa local.

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
ollama list                         # deben aparecer qwen3:4b-instruct-2507-q4_K_M y qwen3:4b
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
| `TAMA_LOCAL_MODEL` | `qwen3:4b-instruct-2507-q4_K_M` | Modelo local |
| `TAMA_NUM_CTX` | `8192` | Contexto por petición (siempre explícito) |
| `TAMA_NUM_GPU` | `0` | Capas en GPU; 0 = solo CPU y RAM |
| `TAMA_THINK` | `false` | Razonamiento del modelo (lento en CPU) |
| `TAMA_TIMEOUT` | `300` | Segundos máximos por respuesta |
| `TAMA_DATA_DIR` | Docker: `/data` (volumen `tama-data`) | Datos de Tama (`events.db`, memoria) |
| `OLLAMA_API_KEY` | vacío | Opcional: nube de Ollama directo a ollama.com (sin ella, vía `ollama signin`) |
| `TAMA_CLOUD_MODEL` | `gemma4:31b-cloud` | Modelo de la nube de Ollama |
| `ANTHROPIC_API_KEY` | vacío | Activa la capa de Claude |
| `TAMA_CLAUDE_MODEL` | `claude-haiku-5-5` | Modelo de Claude |
| `TAMA_CLAUDE_EFFORT` | `low` | Esfuerzo de razonamiento de Claude (`low` = más rápido) |
| `TAMA_CLAUDE_MONTHLY_USD` | `5` | Tope de gasto mensual en Claude |
| `TAMA_*_FIRST_TOKEN_S` | `8` | Tiempo máximo hasta el primer token (nube y Claude) |
| `TAMA_HISTORY_MAX_TOKENS` | `2000` | Historial máximo que se envía al modelo |

La lista completa, con comentarios, está en `.env.example`.

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
