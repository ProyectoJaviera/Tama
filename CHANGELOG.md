# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

### Añadido

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
