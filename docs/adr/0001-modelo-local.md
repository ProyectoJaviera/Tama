# 0001. Modelo local principal y de fondo

- Estado: aceptado
- Fecha: 2026-10-24
- Decisores: Renzo

## Contexto y problema

Tama debe funcionar 100% sin internet, así que necesita un modelo local que converse en español
y use herramientas de forma fiable. El PC es un i5-10400F con 32 GB de RAM y una GTX 660 de 2 GB
que **no se usa** (no sirve para modelos y se asoció a pantallazos azules): todo corre en CPU y RAM,
con un solo modelo cargado a la vez (`OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`).

¿Qué modelo usar como principal (conversación con Renzo) y cuál para tareas de fondo (reflexión,
resúmenes, diario), donde la latencia importa poco?

## Factores de decisión

- Herramientas: % de llamadas correctas en 10 casos simples en español.
- Latencia percibida: tiempo hasta tener la llamada a herramienta o la respuesta.
- Velocidad de generación y de procesamiento de prompt en CPU.
- RAM, con margen para Docker, el navegador y el resto del sistema.

## Opciones consideradas

Medidas en `bench/RESULTS.md` (solo CPU, `num_ctx` 4096, thinking apagado, prompt de ~1.500 tokens):

| Modelo | Prompt (tok/s) | Generación (tok/s) | RAM (GB) | Herramientas | Latencia herramientas (s) |
|---|---:|---:|---:|---:|---:|
| qwen3:1.7b | 124.8 | 15.4 | 1.8 | 70% | 2.3 |
| qwen3:4b | 47.4 | 6.7 | 3.0 | 100% | 30.0 |
| qwen3:8b | 28.5 | 4.3 | 5.5 | 100% | 7.2 |
| qwen3:4b-instruct-2507 | 46.4 | 6.3 | 3.0 | 90% | 5.0 |
| gemma4:31b-cloud (referencia) | n/d | n/d | nube | 100% | 0.5 |

Nota: el `qwen3:4b` de Ollama es la versión 2507 que **siempre razona** antes de responder, aunque
se pida `think=false`; por eso su latencia es de 30 s pese a generar más rápido que el 8B.

## Decisión

- **Principal: `qwen3:8b`.** 100% de herramientas y la menor latencia útil (7 s) porque respeta
  `think=false`. Genera a 4,3 tok/s, lento pero legible en streaming. Usa 5,5 GB de RAM.
- **Fondo: `qwen3:4b`.** 100% de herramientas con 3 GB de RAM; su razonamiento obligatorio y la
  latencia de 30 s no importan en tareas que corren mientras Renzo no está.
- **Descartado: `qwen3:1.7b`.** Rápido, pero falla 3 de 10 llamadas (no usa herramientas cuando
  debería), lo que no es aceptable ni siquiera para tareas de fondo.

## Consecuencias

- Positivas: herramientas fiables sin internet; RAM holgada (5,5 GB de 32 GB).
- Negativas: respuestas largas del modelo local tardan (≈25 s por 100 tokens). La nube (capa 1 de
  la cascada, Sprint 1) cubre la experiencia rápida cuando hay internet.
- Cambiar entre principal y fondo implica recargar el modelo (4-11 s), porque solo hay uno cargado.
- `qwen3:4b-instruct-2507` (evaluado después): genera un 47% más rápido que el 8B (6,3 vs
  4,3 tok/s) y llama herramientas en 5 s, con 3 GB de RAM. Su único fallo fue calcular "el 15% de
  2340" de cabeza (bien: 351) en vez de usar `calculate`, lo que según las reglas se corrige con la
  descripción de la herramienta. Es candidato a principal si Renzo prioriza la velocidad; el cambio
  es solo `TAMA_LOCAL_MODEL`.
