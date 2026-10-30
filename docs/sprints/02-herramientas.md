# Sprint 2: Herramientas (MCP locales)

Estado: en curso (desde 2026-10-30)

## Objetivo
Tama usa herramientas reales mediante servidores MCP locales, instalados una sola vez y con versión fija. Si un servidor falla, Tama sigue conversando. La capa nube se valida con muchas herramientas a la vez y se cambia de modelo si no cumple.

## Alcance
0. Arrastre del Sprint 1. Precargar el modelo local en segundo plano (keep_alive) apenas la detección de conexión vea que no hay red, para que la primera respuesta local no tarde 13 s. Mantenerlo caliente si sobra RAM (configurable). Agregar un test.
1. Cliente MCP en Tama. Usar el SDK oficial mcp de Python. Los servidores se lanzan por stdio como subprocesos, con su comando en config/mcp.toml. Nada de npx ni descargas al arrancar. Las dependencias de los servidores van fijadas en uv.lock, y sus wheels se copian en vendor/ (script scripts/vendor.py) para poder reinstalar sin red.
2. Servidores iniciales, preferentemente en Python para no meter Node en la imagen:
   - tiempo: el servidor oficial mcp-server-time (fecha, hora y zona America/Santiago).
   - notas: propio con FastMCP. Crear, listar, buscar (FTS5), leer, editar y borrar notas en data/notas.db.
   - archivos: propio con FastMCP. Listar, leer, buscar y escribir dentro de una carpeta de trabajo montada (TAMA_WORKDIR), sin salir nunca de ella (validar las rutas).
   - web (opcional, solo con red): el servidor oficial mcp-server-fetch para leer una URL. Sin red se marca como no disponible.
3. Salud y cortacircuitos por servidor. Chequeo de salud al iniciar (list_tools con un tiempo máximo). Un cortacircuitos por servidor (3 fallos → fuera de servicio por 5 minutos). Si un servidor cae, sus herramientas desaparecen del prompt y Tama lo dice si se le pide algo que dependía de él. /herramientas en la CLI muestra el estado.
4. Formato común de herramientas. Convertir los esquemas MCP al formato de cada proveedor (Ollama y Claude). El bucle de herramientas tiene un máximo de 5 pasos por turno. Los resultados se truncan a un tamaño razonable para cuidar el contexto.
5. Confirmación humana. Las herramientas destructivas (borrar o sobrescribir notas o archivos) llevan la anotación destructiveHint, y la CLI pide confirmación (s/n) antes de ejecutarlas. Ninguna acción destructiva corre sin un "s".
6. Pocas herramientas a la vista. Máximo 8 a 10 herramientas visibles por turno, con un selector simple por servidor según el estado y la configuración. Nada de regex de intenciones (regla 3).
7. Benchmark de la capa nube con muchas herramientas (advertencia de Renzo: gemma4:31b-cloud podría fallar con muchos MCP):
   - Batería de herramientas realistas (las reales más otras de relleno plausibles, de varios "servidores") en escalones de 5, 10, 20 y 40 herramientas.
   - Por escalón, más de 20 casos: elegir la herramienta correcta, armar bien los argumentos, encadenar 2 o 3 llamadas y no llamar herramientas cuando no corresponde.
   - Comparar gemma4:31b-cloud con los demás modelos cloud de Ollama que soporten herramientas, usados a través de la sesión de Renzo. Métricas: % de aciertos por escalón, latencia y errores de formato.
   - Si Ollama limita o bloquea el uso de modelos cloud (cuota, límite o plan), detenerse y avisar a Renzo antes de seguir.
   - Resultados en bench/TOOLS_RESULTS.md. Si gemma4 no cumple, proponer el reemplazo en el ADR 0002, y Renzo decide.
8. Evals de herramientas. Más de 20 escenarios nuevos (hora, crear y buscar notas, leer un archivo, pedido destructivo que debe pedir confirmación, pedido sin herramienta) en las 3 capas.

## Fuera de alcance
Memoria y grafo (S3), interfaz web (S5) y voz.

## Criterio de cierre
- 90% o más de llamadas correctas en las 3 capas con el set real de herramientas.
- Con internet cortado y un servidor MCP apagado, Tama sigue conversando y usa los demás.
- La primera respuesta local tras un corte de red empieza en menos de 3 s (gracias a la precarga).
- Docker: los servidores corren dentro del contenedor y la carpeta de trabajo está montada como volumen.
- Demo de Renzo.
