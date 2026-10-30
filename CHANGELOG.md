# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

### Añadido

- Sprint 1: cerebro en cascada (nube de Ollama → Claude → Ollama local) con tiempo máximo al
  primer token, cortacircuitos, detección de conexión y tope de gasto mensual para Claude.
- Interfaz única de proveedores (`tama.providers`) con eventos comunes.
- Identidad de Tama versionada en `src/tama/prompts/identidad.md`.
- Registro de cada turno en `events.db` (SQLite, WAL, migraciones con `PRAGMA user_version`).
- CLI con `/proveedor`, `/salir`, capa en gris bajo cada respuesta y recorte del historial.
- Evals de conversación: 20 escenarios, juez LLM, modos con y sin red y por capa (`--capas`),
  resultados en `evals/RESULTS.md`.
- La nube de Ollama usa la sesión del Ollama local (`ollama signin`); `OLLAMA_API_KEY` es opcional.

- Esqueleto del repo con `uv`, `ruff`, `pytest` y CI en GitHub Actions (Windows y Linux).
- CLI mínima `tama` que conversa con el modelo local de Ollama en streaming, con chequeo de
  salud al iniciar (`tama --check`).
- `Dockerfile` y `docker-compose.yml`: Tama en un contenedor, Ollama nativo en el host.
- Configuración con `pydantic-settings` y `.env.example`.
- Benchmark de modelos `bench/models.py` (velocidad, RAM y llamadas a herramientas), solo CPU,
  con resultados en `bench/RESULTS.md`.
- ADR 0001: `qwen3:4b-instruct-2507-q4_K_M` como modelo local principal (alternativa `qwen3:8b`) y
  `qwen3:4b` para tareas de fondo.

### Corregido

- Tama siempre envía `num_ctx` (8192) y `num_gpu` 0: no hereda el contexto global de Ollama
  (262.144) ni usa la GPU.
- Con Avast, la variable `SSLKEYLOGFILE` apuntaba a su driver y hacía caer a Python/OpenSSL;
  Tama la quita de su propio proceso.
