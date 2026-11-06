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
   (keep_alive) en el chat, la precarga y la reflexión. La web muestra "preparando" mientras tanto.
   Aquí solo se arregla el código, con tests; la meta de responder en menos de 30 s tras abrir sin
   red se mide en `13-modelo-local-v2.md`.
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
- La revisión de planes de la reflexión: tests con modelo simulado; su calidad, con la nube
  (`python -m evals.pulido planes-reflexion`).
- Sin regresión: herramientas, memoria, conversación y reflexión.

## Pasos de la demo
Con una memoria de demo aparte (`demo-s8\`): tus datos de `data\` y `memoria\` no se tocan.

1. En PowerShell, desde `D:\Estudio\IA\Proyecto_Tama\Tama-s8`, con Ollama abierto:
   ```powershell
   $env:TAMA_DATA_DIR="demo-s8\data"; $env:TAMA_MEMORY_DIR="demo-s8\memoria"; $env:TAMA_WORKDIR="demo-s8\trabajo"
   uv run tama web
   ```
   Abre http://127.0.0.1:8765. Al abrir, Tama precarga el modelo local en segundo plano (la CPU
   sube uno o dos minutos) y lo deja cargado.
2. Fechas: cuéntale "El 3 de marzo me operaron de la rodilla" y luego "Una vez tuve alergia a la
   penicilina". Escribe `/memoria historial salud`: la alergia dice "fecha no indicada". Pregunta
   "cuéntame mi historial médico, en orden": la alergia debe salir sin fecha inventada.
3. Planes: "El sábado voy a la playa con Ana" y "Me voy a disfrazar de Shrek para la fiesta de la
   oficina". Escribe `/memoria Renzo`: los dos aparecen como plan, el de la playa "para el 7 de
   noviembre de 2026" y el de Shrek sin fecha.
4. `/memoria` lista las fichas. `/memoria Ana` (o `/memoria ana`, o con un error de tipeo) abre
   la ficha por su nombre. En la pestaña Memoria, un clic en una ficha deja la dirección
   `/memoria/ficha/Ana`; con más de 20 datos aparecen Anterior y Siguiente.
5. Avisos: "Avísame cuando empiece a llover": Tama debe decir que no puede y ofrecer un
   recordatorio a una hora.
6. Opcional, sin red: cierra Tama (Ctrl+C), escribe `ollama stop qwen3:4b-instruct-2507-q8_0`,
   desconecta el WiFi y vuelve a abrir con el paso 1. En el chat aparece "Sin internet: preparando
   el modelo local… (N s)" hasta que esté listo. (El tiempo no se mide en este sprint: va en el
   Sprint 13.)
7. Al terminar, borra `demo-s8\` si quieres.

## Criterio de cierre
- Tests y CI en verde.
- Evals del sprint solo en la nube y sin Claude. Sin regresión en las anteriores.
- Ninguna prueba de calidad ni de velocidad del modelo local (decisión de Renzo, 2026-11-06): todo
  eso queda anotado en `13-modelo-local-v2.md`, incluido el arranque en frío.
- Ningún dato antiguo se pierde (migraciones sin borrar nada).
- Demo de Renzo con una memoria separada de la suya.
