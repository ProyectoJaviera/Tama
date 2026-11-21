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

## Agregado tras la demo: varios adjuntos por mensaje (pedido de Renzo, 2026-11-20)
Hasta 10 archivos y 60 MB en total por mensaje (`TAMA_ATTACHMENTS_MAX_PER_MESSAGE`,
`TAMA_ATTACHMENTS_MAX_TOTAL_MB`). Selección múltiple, arrastrar y soltar en el PC, miniaturas con
Quitar, estado y error por archivo (un archivo que falla no se lleva a los demás). `ver_imagen`
mira hasta 5 fotos juntas en una sola llamada de visión; `resumir_documento` hace un resumen por
documento y el modelo los combina. Eval con 2 PDF y 1 foto: usa las dos herramientas y trae los
datos de los tres (6/6); juez 4, 5, 4, 4, 2 (el 2: no puede verificar lo que no ve). US$ 0,006.

## Fuera de alcance
Video, audio adjunto, Word y Excel, y la elección final del modelo local de visión (S13).

## Evals
- Tests con modelos simulados: subir y guardar sin pisar archivos, límites de tamaño y tipo,
  extracción de PDF, achicar fotos, cascada de visión (Claude falla o sin red → local), tope de
  gasto, rutas remotas, nota del adjunto en el mensaje.
- Evals con Claude (modelo y juez, tope US$ 0,20 por tanda): describir una foto de taller y
  resumir un PDF de 20 páginas; corrección de un dato.

## Resultados de evals (Claude como modelo y juez)
- Foto de taller: usa `ver_imagen` y nombra las herramientas (6/6 palabras clave); juez 5, 5, 5, 3
  (el 3: agrega un detalle que no verifica, como "espacio libre en el panel").
- PDF de 20 páginas: usa `resumir_documento` y trae los 5 datos clave con sus páginas (5/5);
  juez 4, 5, 5, 5.
- Corrección de un dato (S11 pendiente): ningún dato antiguo queda vigente; recuerda el nuevo y
  el anterior. La eval ya no cuenta como antiguo el dato vigente que nombra los planes viejos.
- Visión en el PC (humo, `gemma4:e4b`, solo CPU): describe bien una imagen en 19 s.
- Gasto de la tanda: US$ 0,015.

## Pasos de la demo
Con una memoria de demo aparte (`demo-s12\`): tus datos no se tocan. En `demo-s12\muestras\`
hay un manual ficticio de 20 páginas y un dibujo de taller, por si no tienes otros a mano.
1. Cierra el Tama del S11 si está abierto (usa el mismo puerto) y abre el del S12 (PowerShell):
   `cd D:\Estudio\IA\Proyecto_Tama\Tama-s12`,
   `$env:TAMA_DATA_DIR = "D:\Estudio\IA\Proyecto_Tama\Tama-s12\demo-s12\data"` y
   `uv run tama web`. Abre http://127.0.0.1:8765 y recarga con Ctrl+F5.
2. "Adjuntar" → una foto de tu taller → aparece la miniatura y el aviso de privacidad. Escribe
   "¿qué ves?" y envía: Tama la describe (Claude).
3. "Adjuntar" → un PDF largo (o el manual de muestra) → "resúmemelo". Luego pregunta algo de una
   página: "¿qué dice de los frenos?".
4. Pídele que guarde algo de lo que vio ("anota que la llave 13 está en el tablero") y revisa
   que quede en Memoria; lo que no pidas no se guarda.
5. En el celular (cierra y abre la app): "Adjuntar" → Cámara → saca una foto → "¿qué es esto?".
6. Sin internet (opcional): desconecta el PC, adjunta una foto y pregunta: la mira Gemma 4 en el
   PC (unos 20 a 60 s); un PDF se sigue leyendo en el PC.

## Criterio de cierre
- Describe una foto del taller y resume un PDF de 20 páginas.
- Sin internet, describe una foto en el PC (humo) y lee un PDF.
- Tests y CI en verde. Demo de Renzo en el PC y en el celular.
