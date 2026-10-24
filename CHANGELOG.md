# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

## [Sin publicar]

### Añadido

- Esqueleto del repo con `uv`, `ruff`, `pytest` y CI en GitHub Actions (Windows y Linux).
- CLI mínima `tama` que conversa con el modelo local de Ollama en streaming, con chequeo de
  salud al iniciar (`tama --check`).
- `Dockerfile` y `docker-compose.yml`: Tama en un contenedor, Ollama nativo en el host.
- Configuración con `pydantic-settings` y `.env.example`.
- Benchmark de modelos `bench/models.py` (velocidad, RAM y llamadas a herramientas).
