# 0003. Búsqueda automática en la memoria para la capa local

- Estado: aceptado
- Fecha: 2026-10-30
- Decisores: coordinación del proyecto, para cumplir el requisito de Renzo de funcionar 100% sin
  internet

## Contexto y problema

La regla del proyecto es que el modelo decide cuándo usar herramientas, y la spec del Sprint 3 dice
que el modelo decide cuándo llamar a `search_nodes`. Nube y Claude lo hacen bien. El modelo local
(`qwen3:4b-instruct-2507`) guarda lo que Renzo cuenta, pero en una sesión nueva casi nunca busca:
responde que no tiene el dato. Sin red, Tama olvidaría casi todo.

## Opciones consideradas

Recuerdo de 10 hechos en sesiones nuevas con el modelo local:

| Opción | Hechos | Comentario |
|---|---:|---|
| Mejorar la identidad y las descripciones | 0-2 | Repite la instrucción en vez de buscar |
| Solo 8 herramientas a la vista | 2 | |
| `qwen3:8b` | 4 | Más lento, y sigue sin buscar en general |
| Nombres de lo guardado en el prompt | 5 | Responde sin detalles y sin buscar |
| `qwen3:4b` con razonamiento | no medido | No terminó en 8 minutos: inviable para conversar |
| Búsqueda automática de Tama | 10 | 3/3 casos negativos sin inventar |

## Decisión

**Solo en la capa local, Tama busca en la memoria antes de responder**, con FTS5 sobre las palabras
del mensaje, y agrega al prompt lo encontrado (máximo ~200 tokens). Si no hay coincidencias, no
agrega nada. Se controla con `TAMA_LOCAL_AUTO_RECALL` (activo por defecto). Nube y Claude siguen
decidiendo solos.

No interpreta la intención del mensaje (no hay regex de intenciones): es una búsqueda de texto. Es
una excepción explícita a la regla de contexto corto, porque el requisito duro es que Tama funcione
sin internet.

## Consecuencias

- Positivas: la memoria funciona sin red.
- Negativas: cada turno local hace una búsqueda y, si hay coincidencias, suma hasta ~200 tokens.
  Las coincidencias por palabras comunes pueden agregar datos que no hacían falta.
- A revisar si un modelo local futuro llama a `search_nodes` por sí mismo.
