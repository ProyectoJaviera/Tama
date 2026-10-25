# Sprint 1: Conversación

Estado: en curso (desde 2026-10-24)

## Objetivo
Tama conversa por CLI con streaming, con identidad propia y un cerebro en cascada que sigue respondiendo cuando falla la nube o se corta internet. Cada turno queda registrado en events.db.

## Alcance
0. Arrastre del Sprint 0. Modelo principal local por defecto: qwen3:4b-instruct-2507 (decisión de Renzo, 2026-10-24). Fondo: qwen3:4b. Actualizar config, compose, .env.example, README y ADR 0001 (en estado "aceptado", con nota de la revisión).
1. Identidad versionada. src/tama/prompts/identidad.md: quién es Tama, tono (cercano, honesto, opina y sugiere, en español de Chile neutro) y límites. Menos de 400 tokens. Se carga desde archivo y nunca se escribe en el código.
2. Interfaz única de proveedores. Provider con stream_chat(messages, tools=None) que devuelve eventos comunes (texto, llamada a herramienta, fin, error). Tres implementaciones: OllamaCloudProvider (modelo configurable, gemma4:31b-cloud por defecto; por defecto vía el Ollama del PC con la sesión de `ollama signin`, sin clave; con OLLAMA_API_KEY, directo a ollama.com), ClaudeProvider (SDK oficial anthropic, modelo Haiku configurable con TAMA_CLAUDE_MODEL, ANTHROPIC_API_KEY) y OllamaLocalProvider (el cliente actual: solo CPU, num_gpu 0, num_ctx acotado). Preferir un adaptador propio a LiteLLM, salvo que surja una razón medida; si se usa LiteLLM, escribir un ADR.
3. Cascada con cortacircuitos. Orden: nube de Ollama, Claude, local. Cada capa tiene un tiempo máximo hasta el primer token (configurable; nube 8 s, Claude 8 s) y un cortacircuitos: 3 fallos seguidos lo abren por 5 minutos y luego queda medio abierto (1 intento). Una capa se salta si falta su API key (Claude) o, en la nube de Ollama, si el daemon responde 401 (sesión no iniciada). Si una capa falla a mitad de una respuesta, se reintenta el turno completo en la siguiente y se avisa en una línea discreta ("seguí en local").
4. Detección de conexión. Chequeo barato (TCP a 1.1.1.1:443 o similar) con caché de 30 s. Sin red, se va directo a local sin esperar timeouts.
5. Tope mensual de Claude. Se cuentan los tokens de entrada y salida por mes en events.db y se calcula el gasto con precios configurables. Al superar TAMA_CLAUDE_MONTHLY_USD (por defecto 5), la capa se desactiva hasta el mes siguiente.
6. events.db. SQLite (WAL) en el volumen de datos. La tabla events guarda id, ts, tipo (user_msg, assistant_msg, provider_switch, error), proveedor, modelo, tokens, latencia hasta el primer token, latencia total y contenido. Migraciones simples con PRAGMA user_version.
7. CLI. tama abre el chat; tama "msg" responde una vez. /proveedor muestra el estado de las capas y /salir termina. Se muestra en gris en qué capa respondió. El historial de la sesión se recorta para no superar ~2.000 tokens de prompt.
8. Evals base. evals/escenarios/*.yaml con 20 escenarios: saludo, opinión pedida, sugerencia, pregunta factual simple, cambio de tema, límite (se niega con tacto), seguimiento de contexto, etc. Un runner (uv run python -m evals) puntúa con un juez LLM (qwen3:4b local, o la nube si hay red) de 1 a 5 por criterio y guarda los resultados en evals/RESULTS.md. Modo --offline para forzar solo la capa local.

## Fuera de alcance
Herramientas y MCP (S2), memoria (S3), interfaz web (S5) y voz (S6).

## Criterio de cierre
- Primer token en menos de 3 s con la nube disponible (p50 y p95 medidos en las evals). En local se mide y se reporta, sin meta.
- Con internet cortado (simulado en tests y real en la demo), Tama sigue respondiendo en local sin esperar timeouts.
- Tests (LLM simulado) para la cascada, el cortacircuitos, el tope de gasto, la detección de conexión y events.db.
- Evals: 20 escenarios ejecutados con y sin red, resultados en evals/RESULTS.md.
- Demo de Renzo: conversar 5 minutos, desconectar la red a mitad y seguir.

## Necesita de Renzo
Sesión iniciada en Ollama (`ollama signin`) para la nube, y ANTHROPIC_API_KEY (console.anthropic.com) en .env. OLLAMA_API_KEY es opcional. Sin ellas, todo se desarrolla y prueba igual: las capas se saltan y Tama responde en local.
