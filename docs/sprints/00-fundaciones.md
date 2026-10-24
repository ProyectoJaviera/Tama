# Sprint 0: Fundaciones

**Estado:** cerrado (2026-10-24, demo aprobada por Renzo)
**Fase:** 0

## Objetivo

Dejar el repo listo para trabajar con calidad desde el primer día y elegir, con datos medidos en el PC de Renzo, el modelo local de Tama.

## Entregables

1. Esqueleto del repo según `CLAUDE.md`: `pyproject.toml` con `uv`, paquete `src/tama/`, `tests/`, `evals/`, `bench/`, `docs/adr/`, `.gitignore` (excluye `.env`, `*.db`, modelos, `memoria/` privada) y `.env.example`.
2. `ruff` y `pytest` configurados, con un test trivial que pase.
3. CI en GitHub Actions: lint + tests en cada push y pull request.
4. `README.md` con qué es Tama, requisitos e instalación (incluye instalar Ollama y descargar los modelos una vez).
5. CLI mínima `uv run tama` que envía un mensaje al modelo local de Ollama y muestra la respuesta en streaming.
6. Script `bench/models.py` que mide, para cada modelo candidato:
   - tokens por segundo de generación y de procesamiento de prompt (con un prompt de ~1.500 tokens),
   - RAM usada,
   - % de llamadas a herramientas correctas en 10 casos simples en español (ej. "¿qué hora es?", "anota que mañana compro filamento").
7. Tabla de resultados en `bench/RESULTS.md` y `docs/adr/0001-modelo-local.md` con la elección.
8. `Dockerfile` (python:3.12-slim, dependencias desde `uv.lock`, usuario no root) y `docker-compose.yml` con un solo servicio `tama` (healthcheck, `restart: unless-stopped`, volumen `tama-data`). Ollama queda nativo en el host; Tama lo alcanza por `OLLAMA_HOST=http://host.docker.internal:11434` con `extra_hosts: host-gateway`. Chequeo de salud al iniciar con mensaje claro si Ollama no responde. README con instalación normal y sin internet (instalador de Ollama + carpeta de modelos + `docker save`).

## Modelos candidatos (ajustar a lo disponible en Ollama)

- Local: un modelo de ~4B, uno de ~8B y uno de ~1,5–3B para tareas de fondo, todos en Q4 y con soporte de tool calling (p. ej. familia Qwen3).
- Cloud: el modelo de Ollama Cloud que se usaría como capa 1, solo para tener la referencia de calidad.

## Criterios de aceptación

- [x] `uv sync && uv run pytest && uv run ruff check .` pasa en limpio en Windows.
- [x] CI en verde en GitHub (Linux y Windows).
- [x] `uv run tama "hola"` responde en streaming con el modelo local **con el cable de red desconectado**.
- [x] `docker compose up` levanta Tama, que responde con el Ollama nativo del host **con la red desconectada**.
- [x] `bench/RESULTS.md` tiene datos reales de los 3 modelos locales (solo CPU).
- [x] ADR 0001 elige el modelo local principal y el de fondo, justificado con la tabla.

## Verificación (2026-10-24)

- Contenedor contra el Ollama nativo: `tama --check` OK y respuesta en streaming con `qwen3:8b`
  al 100% en CPU.
- Sin red, simulado: contenedor sin DNS (internet inalcanzable comprobado en la misma prueba)
  responde igual.
- Sin red, real: Renzo desconectó la red y Tama respondió (demo aprobada, 2026-10-24).

## Fuera de alcance

Cascada de cerebros, memoria, herramientas MCP, interfaz web, voz. Empiezan en S1 en adelante.
