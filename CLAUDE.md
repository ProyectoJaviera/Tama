# Tama: guía para Claude Code

Tama es un asistente de IA personal y acompañante diario de Renzo: conversa, recuerda, opina y sugiere. Proyecto personal, un solo usuario, PC con Windows 10 (i5-10400F, 32 GB RAM, sin GPU útil).

Plan completo: https://claude.ai/artifact/Xbr5Ejk6iYHQbWL8xwDGsN
Sprint en curso: ver `docs/sprints/` (el archivo de número más alto sin marcar como cerrado).

## Reglas no negociables

1. **100% funcional sin internet.** La nube solo mejora la calidad. Todo lo esencial (conversar, memoria, herramientas locales, voz, interfaz) debe funcionar con la red desconectada.
2. **Nada se descarga en ejecución.** Modelos y paquetes se descargan al instalar. Nunca `npx` ni descargas al arrancar. Librerías de modelos en modo offline (`HF_HUB_OFFLINE=1`).
3. **El modelo decide con herramientas.** Prohibido detectar intenciones con regex o keywords. Si el modelo usa mal una herramienta, se mejora la herramienta o su descripción.
4. **Secretos solo en `.env`** (nunca versionado). Config con `pydantic-settings`.
5. **Confirmación humana** antes de borrar, enviar o ejecutar algo destructivo.
6. **Contexto corto.** En CPU cada token cuesta. Prompts bajo ~2.000 tokens; no inyectar memoria "por si acaso".
7. **Sin base de datos vectorial.** Memoria = grafo de conocimiento en SQLite + Markdown.
8. **Simple hasta que duela.** Un proceso, SQLite, archivos. Nada de microservicios sin un problema medido.

## Arquitectura (objetivo)

- Cerebro en cascada: Ollama Cloud (gratis) → API de Claude (Haiku, tope mensual) → Ollama local. Tiempo máximo por capa + cortacircuitos (3 fallos = se salta unos minutos). Tareas de fondo siempre en local.
- Una sola interfaz para los 3 proveedores (formato de herramientas común).
- Herramientas como servidores MCP locales, instalados una vez con versión fija y copia en `vendor/`. Chequeo de salud al iniciar y degradación elegante si uno falla.
- Memoria: `memoria/nucleo.md` (perfil, siempre en el prompt), grafo (entidades, observaciones con fecha, relaciones), `events.db` (episodios), tabla `goals`, diario en Markdown, bucle de reflexión en inactividad.

## Stack

Python 3.12, `uv` (dependencias y entorno), FastAPI (más adelante), SQLite + FTS5, Ollama, `ruff` (lint y formato), `pytest`, GitHub Actions.

## Estructura del repo

```
src/tama/        código (paquete)
tests/           pytest, con LLM simulado
evals/           escenarios + juez LLM; se corren con y sin internet
bench/           benchmarks de modelos
docs/sprints/    una spec corta por sprint
docs/adr/        decisiones caras de revertir (formato MADR)
vendor/          copias de paquetes de terceros (MCP)
```

## Cómo trabajar

- Ciclo: spec del sprint → rama corta → tests → evals + demo → OK de Renzo → merge a `main`.
- Commits con Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Definición de terminado: tests verdes, evals sin regresión **con y sin internet**, README y CHANGELOG al día, demo aprobada por Renzo.
- Código e identificadores en inglés; documentación, prompts de Tama y mensajes al usuario en español.
- No escribir documentos de "certificación". Los números salen de tests y evals.

## Comandos

```
uv sync                 # instalar dependencias
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # formato
```
