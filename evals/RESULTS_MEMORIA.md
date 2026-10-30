# Evals de memoria

Generado por `python -m evals.memoria` con datos ficticios. Día 1: 10 hechos en una
conversación natural. Se reinicia Tama. Día 2: una pregunta por hecho, cada una en una
sesión nueva. Casos negativos: datos nunca contados. Meta: 9 de 10 y ninguna invención.

| Capa | Hechos recordados | Negativos | Entidades | Duplicadas | Mal nombradas | Tokens de prompt (máx) |
|---|---:|---:|---:|---:|---:|---:|
| nube | 9/10 | 3/3 | 10 | 1 | 0 | 2047 |

## Fallos

- `nube` · ¿Qué hierba no me gusta?: No tengo anotado que haya alguna hierba que no te guste. Solo recuerdo que prefieres el té verde antes que el café por l
- `nube` · duplicada: lucia / mudanza de lucia
