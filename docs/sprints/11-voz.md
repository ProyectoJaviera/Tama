# Sprint 11: Voz

Estado: en curso

## Objetivo
Hablarle a Tama y escucharla, en el PC y en el celular, también sin internet.

## Alcance
1. Escuchar: whisper.cpp en CPU (`pywhispercpp` 1.5.1, sin GPU). Modelo `ggml-base-q5_1`
   descargado una vez con versión y hash fijos (`scripts/descargar_voz.py`, en `models/voz/`, fuera
   del repo); nunca en ejecución. El navegador manda el audio ya en WAV de 16 kHz mono, así no hace
   falta ffmpeg. Una pista de vocabulario ("Tama") ayuda a escribir bien el nombre.
2. Hablar en cascada: ElevenLabs (`eleven_flash_v2_5`, API key y voice_id en `.env`) → Piper local
   (`piper-tts` 1.8.0, voz `es_MX-ald-medium` con hash fijo) si no hay red, falta la key o la
   voz, se pasa el tope del mes, el cortacircuitos está abierto o la API falla. El tope mensual de
   caracteres (`TAMA_ELEVENLABS_MONTHLY_CHARS`, 10.000 por defecto) se cuenta en el PC
   (`data/voz.db`). La key solo viaja en la cabecera `xi-api-key`: nunca en logs ni mensajes.
3. Voz propia para Tama, con comandos que ejecuta Renzo en su cuenta de ElevenLabs:
   - `tama voz disenar ["descripción"]`: Voice Design crea voces originales desde una descripción
     (por defecto, serena y analítica en español neutro, sin nombrar ni imitar a nadie). Renzo
     escucha las pruebas, elige una y queda guardada; el comando muestra la línea para el `.env`.
   - `tama voz listar`: voces propias y de la biblioteca en español.
   - `tama voz clonar <wav>`: solo para una voz propia o con permiso explícito de su dueño. Pide
     confirmarlo, revisa que el plan permita clonar, recorta a 2 minutos y normaliza.
   - `tama voz probar ["texto"]`: dice algo con la voz activa y guarda el audio.
   No se clona la muestra "Raphael/Ciel" del backup: es la voz de un personaje interpretado por un
   actor real, sin su permiso.
4. Web y PWA: botón "Hablar" (mantener apretado) con el micrófono del navegador, que en el
   celular funciona por el HTTPS de Tailscale, y "Leer respuestas en voz alta" (se recuerda en el
   dispositivo). Las rutas de voz respetan el login y las restricciones del S10.
5. Estado muestra la voz activa (ElevenLabs o Piper, y por qué), los caracteres del mes y el tope.
6. Al abrir Tama, Piper y whisper se cargan en segundo plano: la primera respuesta hablada no
   espera los ~5 s de carga de Piper.

## Fuera de alcance
La calidad del modelo local y la elección final del tamaño de whisper (`13-modelo-local-v2.md`).

## Evals
- Tests: cascada (ElevenLabs falla → Piper y el cortacircuitos se abre; tope → Piper; sin red o
  sin key → Piper), la key nunca aparece en errores ni logs, pedido con flash y la cabecera,
  audio a 16 kHz, configuración antigua de Piper, rutas web de voz y su login, `disenar`
  (elige y guarda), `clonar` (pide permiso, revisa el plan, recorta) con la API simulada.
- `bench/voz_ida_vuelta.py`: ida y vuelta hablada con la nube y Piper en frases cortas.
- Evals sin regresión en la nube, sin Claude.

## Pasos de la demo
Con una memoria de demo aparte (`demo-s11\`): tus datos no se tocan. Los modelos de voz ya están
descargados en `Tama-s11\models\voz` (en otra carpeta: `uv run python scripts/descargar_voz.py`).
En PowerShell, desde `D:\Estudio\IA\Proyecto_Tama\Tama-s11`.

En el PC:
1. Cierra la Tama abierta (Ctrl+C) y abre esta:
   ```powershell
   $env:TAMA_DATA_DIR="demo-s11\data"; $env:TAMA_MEMORY_DIR="demo-s11\memoria"; $env:TAMA_WORKDIR="demo-s11\trabajo"
   uv run tama web
   ```
2. En http://127.0.0.1:8765, marca "Leer respuestas en voz alta". Mantén apretado "Hablar", di
   "Hola Tama, ¿qué hora es?" y suelta (la primera vez Chrome pide el micrófono). Debe aparecer
   tu mensaje escrito, la respuesta y su voz (Piper, sin key todavía).
3. Estado > Voz: "Piper (local)", con el motivo, y 0 de 10.000 caracteres.

ElevenLabs (cuando tengas la key):
4. En tu `.env` (el de `Tama-s11`), agrega `ELEVENLABS_API_KEY=<tu key>`.
5. `uv run tama voz disenar`: escucha las voces de prueba (se abren solas), elige un número y pega
   en el `.env` la línea `TAMA_ELEVENLABS_VOICE_ID=...` que muestra. Reinicia Tama (paso 1).
6. `uv run tama voz probar` y Estado deben decir ElevenLabs; los caracteres del mes suben.

En el celular (datos móviles, Tailscale conectado):
7. Esta memoria de demo no tiene passkeys: en el PC, Estado > "Generar código para un celular";
   en el celular, "Registrar esta passkey" con el código y luego entra.
8. Marca "Leer respuestas en voz alta", mantén "Hablar" (el celular pide el micrófono), habla y
   suelta: escribe tu mensaje, responde y lo dice en voz alta.

Sin internet (opcional): desconecta el PC y repite el paso 2 en el PC: escucha y habla con
whisper y Piper. Al terminar, puedes borrar `demo-s11\`.

## Criterio de cierre
- Tests y CI en verde.
- Ida y vuelta hablada en menos de 6 s en frases cortas, con la nube.
- Sin internet, Tama escucha y habla con whisper y Piper.
- Demo de Renzo en el PC y en el celular.
