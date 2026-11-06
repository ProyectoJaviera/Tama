# Sprint 8: Pulido de la v1

Estado: en curso

## Objetivo
Cerrar los pendientes que dejaron los sprints anteriores antes del uso real: fechas que no se
inventan, planes que no se pierden, un arranque sin red que no hace esperar y una memoria que se
puede revisar completa.

## Alcance
1. "Fecha no indicada": la línea de tiempo que recibe el modelo ya viene redactada, con "fecha no
   indicada" en cada dato sin fecha, y las búsquedas tampoco hacen pasar el día en que se contó por
   la fecha del hecho. Es determinista: no depende de que el modelo lo diga.
2. Planes futuros ("me voy a disfrazar de Shrek"): un tipo de dato propio (plan), con su fecha
   futura si la hay. La reflexión revisa la conversación en busca de planes no guardados. Lo decide
   el modelo, sin regex de intención.
3. Arranque sin red: Tama precarga el modelo local en segundo plano al abrirse y lo mantiene cargado
   (keep_alive). La web muestra "preparando" mientras tanto. Meta: responde en menos de 30 s tras
   abrir, sin red.
4. `/memoria`: todos los datos de una ficha, con paginación, y la ficha se pide por su nombre (no
   por número), en el chat y en la web.
5. Arrastre del S4:
   - "¿Tienes alguna sugerencia?" revisa primero las pendientes.
   - Cada observación dice de quién es.
   - Tama no promete avisos que no puede dar.
   - La reflexión registra cada pasada en events.db, aunque no cambie nada.

## Fuera de alcance
Voz, remoto/PWA, borrar modelos descartados y la calidad del modelo local para conversar (va en
`13-modelo-local-v2.md`).

## Evals
- Tests: la línea de tiempo y las búsquedas con "fecha no indicada"; planes con y sin fecha;
  paginación y búsqueda por nombre de `/memoria`; de quién es cada dato; registro de cada pasada.
- `python -m evals.pulido` en la nube y sin Claude: historial con un evento sin fecha, planes
  futuros (varios ejemplos), sugerencias pendientes y avisos que no se pueden dar.
- `python -m evals.pulido planes-reflexion`: la reflexión, en local, encuentra planes no guardados.
- Arranque sin red: `bench/cold_start.py`, en frío y con el modelo ya cargado.
- Sin regresión: herramientas, memoria, conversación y reflexión.

## Pasos de la demo
(se completan al cierre)

## Criterio de cierre
- Tests y CI en verde.
- Evals del sprint en la nube sin Claude; reflexión en local. Sin regresión en las anteriores.
- Ningún dato antiguo se pierde (migraciones sin borrar nada).
- Demo de Renzo con una memoria separada de la suya.
