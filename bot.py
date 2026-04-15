"""
bot.py
======
Handler de Slack y punto de entrada del bot.
Orquesta la extracción de texto, generación y validación del JSON.

Módulos del proyecto:
  extractor.py  — extracción de texto (markitdown + OCR) y llamada a Claude
  builder.py    — construcción del JSON con plantillas fijas
  validator.py  — validación del JSON antes de subirlo
  config.py     — constantes compartidas (ICON_MAP, PROMPTS, etc.)

Variables de entorno requeridas (.env):
  SLACK_BOT_TOKEN   — token del bot de Slack (xoxb-...)
  SLACK_APP_TOKEN   — token de la app en modo socket (xapp-...)
  ANTHROPIC_API_KEY — clave de API de Anthropic
"""

import os
import json
import logging
import tempfile
import threading
import requests
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from extractor import file_to_text, extract_subsidies
from builder import build_json
from validator import validate_json

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("bot")

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def process_file(event, client, say):
    """Procesa el archivo en un hilo separado para evitar que Slack
    reintente el evento por timeout (Claude puede tardar más de 3 segundos).
    """
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")

    file_info = client.files_info(file=file_id)
    file = file_info["file"]
    filename = file["name"]

    logger.info(f"Archivo recibido: {filename}")

    SUPPORTED = (".pdf", ".docx", ".doc", ".pptx", ".xlsx")
    if not any(filename.lower().endswith(ext) for ext in SUPPORTED):
        logger.warning(f"Formato no soportado: {filename}")
        say(channel=channel_id, text=f"Formato no soportado. Subí un archivo: {', '.join(SUPPORTED)}")
        return

    say(channel=channel_id, text="Recibí el archivo, procesando... un momento")

    file_url = file["url_private_download"]
    headers = {"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}
    response = requests.get(file_url, headers=headers)

    logger.info("Extrayendo texto del archivo...")
    try:
        text = file_to_text(response.content, filename)
        logger.info(f"Texto extraído: {len(text)} caracteres")
    except Exception as e:
        logger.error(f"Error al extraer texto: {e}")
        say(channel=channel_id, text=f"Error al leer el archivo: {e}")
        return

    if not text:
        logger.warning("No se pudo extraer texto del archivo")
        say(channel=channel_id, text="No pude extraer texto del archivo.")
        return

    logger.info("Llamando a Claude Haiku para extraer subsidios...")
    try:
        subsidies = extract_subsidies(text)
        logger.info(f"Subsidios extraídos: {len(subsidies)}")
        result = build_json(subsidies)
        logger.info(f"JSON construido: {len(result['pages'])} páginas")
    except Exception as e:
        logger.error(f"Error al generar el JSON: {e}")
        say(channel=channel_id, text=f"Error al generar el JSON: {e}")
        return

    logger.info("Validando JSON...")
    errors = validate_json(result)
    if errors:
        logger.warning(f"Validación fallida: {errors}")
        error_list = "\n".join(f"• {e}" for e in errors)
        say(channel=channel_id, text=f"El JSON generado tiene errores:\n{error_list}")
        return

    logger.info("Validación OK — subiendo archivo...")
    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
        tmp.write(result_json)
        tmp_path = tmp.name

    client.files_upload_v2(
        channel=channel_id,
        file=tmp_path,
        filename=f"{filename.replace('.pdf', '')}.json",
        initial_comment="JSON generado exitosamente!"
    )

    os.unlink(tmp_path)
    logger.info(f"JSON subido exitosamente para: {filename}")


@app.event("file_shared")
def handle_file_shared(event, client, say):
    """Recibe el evento de Slack y delega el procesamiento a un hilo separado.
    Retorna inmediatamente para evitar que Slack reintente el evento por timeout.
    """
    threading.Thread(target=process_file, args=(event, client, say)).start()


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
