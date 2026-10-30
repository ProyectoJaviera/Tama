# 0002. Modelo de la capa nube con muchas herramientas

- Estado: aceptado
- Fecha: 2026-10-30
- Decisores: Renzo

## Contexto y problema

La capa 1 de la cascada usa `gemma4:31b-cloud` a través del Ollama local (sesión de
`ollama signin`, plan gratuito). Por experiencia en otro proyecto, Renzo advirtió que ese modelo
podría fallar cuando tiene muchos servidores MCP conectados. ¿Sirve como capa nube ahora que Tama
usa herramientas, o hay que cambiarlo por otro modelo cloud de Ollama?

## Factores de decisión

- % de casos correctos con muchas herramientas a la vista: elegir la herramienta, armar los
  argumentos, encadenar 2-3 llamadas y no llamar cuando no corresponde.
- Errores de formato en las llamadas y latencia.
- Que el modelo esté incluido en el plan de Ollama de Renzo.

## Opciones consideradas

Medido con `bench/tools_bench.py` (resultados en `bench/TOOLS_RESULTS.md`): 21 casos en el
escalón de 5 herramientas y 29 en los demás; las 10 herramientas reales de Tama más relleno
plausible, con varias parecidas a propósito (p. ej. `todo_add` y `reminder_create` frente a
`create_note`).

| Modelo | 5 | 10 | 20 | 40 | Latencia | Errores de formato |
|---|---:|---:|---:|---:|---:|---:|
| gemma4:31b-cloud | 100% | 100% | 100% | 93% | 0,6 s | 0 |
| gpt-oss:20b-cloud | — | — | — | 90% | 1,2 s | 0 |
| gpt-oss:120b-cloud | — | — | — | 86% | 0,7 s | 0 |
| deepseek-v4-pro:cloud | no incluido en el plan gratuito | | | | | |

El catálogo de Ollama tiene 21 modelos cloud con herramientas. La comparación se detuvo cuando
Ollama rechazó `deepseek-v4-pro:cloud` por el plan; Renzo decidió no seguir comparando.

## Decisión

**Se mantiene `gemma4:31b-cloud` como capa nube.** Es el mejor de los modelos medidos en todos los
escalones, no tiene errores de formato, es el más rápido y está incluido en el plan gratuito. En
Tama nunca ve más de 10 herramientas por turno (selector del Sprint 2), donde acertó el 100%.

## Consecuencias

- Positivas: sin cambios en la cascada ni costos nuevos; la advertencia de Renzo quedó medida.
- A vigilar: sus 2 fallos con 40 herramientas fueron usar `todo_add` en vez de `create_note` para
  "anota que...". Si esa confusión aparece con las herramientas reales, se mejoran las
  descripciones de las herramientas (regla 3), no se agrega lógica de intenciones.
- Si en el futuro Tama necesita más de 10 herramientas a la vez, repetir el benchmark con
  `bench/tools_bench.py` antes de subir el máximo.
