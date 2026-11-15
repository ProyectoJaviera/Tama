# Sprint 12: Sentidos (fotos y documentos)

Estado: en curso

## Objetivo
Mostrarle a Tama una foto o un documento, desde el PC o el celular, y que lo describa, lo lea o
lo resuma. Sin internet también, más lento.

## Alcance
0. Pendiente del S11: al corregir un plan compartido ("con mi esposa iremos de piratas"), el dato
   antiguo de la otra persona quedaba vigente. `update_observation` muestra los datos del tema que
   siguen vigentes y pide revisarlos en la misma respuesta; su descripción pide los ids de todas
   las personas del plan. Lo decide el modelo (regla 3).
1. Adjuntos: botón "Adjuntar" en el chat (en el celular abre la cámara o la galería). Fotos JPG,
   PNG o WebP de hasta 15 MB; PDF de hasta 30 MB y 300 páginas; texto o Markdown de hasta 2 MB.
   Se guardan en `data/adjuntos/AAAA-MM/` con un registro en `data/adjuntos.db`. Nunca se borran
   ni se sobrescriben. El mensaje lleva una nota con el id del adjunto para el modelo.
2. Herramientas (servidor MCP local `adjuntos`): `ver_imagen` (describe o responde sobre una
   foto), `leer_documento` (texto de un rango de páginas), `resumir_documento` (resumen por partes)
   y `listar_adjuntos`. El modelo decide cuándo usarlas.
3. Visión en cascada: Claude Haiku (con el mismo tope de gasto que la capa de Claude) → modelo
   local de visión en CPU (`gemma4:e4b`, ya instalado; su elección final queda para el S13). La
   foto se achica a 1568 px antes de enviarla. Privacidad: al adjuntar una foto se avisa que se
   describe con Claude (sale a la nube) y que sin internet se describe en el PC
   (`TAMA_VISION_CLOUD=false` la deja siempre en el PC).
4. Documentos en local: texto de los PDF con `pypdfium2`; una página sin texto (escaneada) se lee
   con el modelo de visión, solo si hace falta. Los resúmenes usan la cascada.
5. Desde fuera del PC (Tailscale, con passkey): se pueden subir y ver adjuntos, porque solo tocan
   la carpeta de adjuntos, no los archivos del PC; lo demás del S10 sigue bloqueado.
6. Memoria: lo visto en un adjunto no se guarda solo; se guarda si Renzo lo pide.

## Fuera de alcance
Video, audio adjunto, Word y Excel, y la elección final del modelo local de visión (S13).

## Evals
- Tests con modelos simulados: subir y guardar sin pisar archivos, límites de tamaño y tipo,
  extracción de PDF, achicar fotos, cascada de visión (Claude falla o sin red → local), tope de
  gasto, rutas remotas, nota del adjunto en el mensaje.
- Evals con Claude (modelo y juez, tope US$ 0,20 por tanda): describir una foto de taller y
  resumir un PDF de 20 páginas; corrección de un dato.

## Criterio de cierre
- Describe una foto del taller y resume un PDF de 20 páginas.
- Sin internet, describe una foto en el PC (humo) y lee un PDF.
- Tests y CI en verde. Demo de Renzo en el PC y en el celular.
