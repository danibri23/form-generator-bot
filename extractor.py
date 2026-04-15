"""
extractor.py
============
Extracción de texto desde archivos y llamada a Claude Haiku
para obtener la lista estructurada de subsidios.
"""

import os
import json
import logging
import tempfile
import anthropic
from markitdown import MarkItDown
import pytesseract
from pdf2image import convert_from_path

from config import EXTRACT_PROMPT

logger = logging.getLogger("extractor")
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def ocr_pdf(tmp_path: str) -> str:
    """Extrae texto de un PDF escaneado usando OCR (pytesseract + poppler).
    Se usa como fallback cuando markitdown no puede extraer texto
    (PDFs basados en imágenes en lugar de texto seleccionable).
    Requiere: brew install poppler tesseract tesseract-lang
    """
    images = convert_from_path(tmp_path)
    text = ""
    for image in images:
        text += pytesseract.image_to_string(image, lang="spa") + "\n"
    return text.strip()


def file_to_text(file_bytes: bytes, filename: str) -> str:
    """Convierte el contenido de un archivo a texto plano.
    Intenta primero con markitdown (soporta PDF, DOCX, PPTX, XLSX).
    Si markitdown no extrae texto (PDF escaneado), usa OCR como fallback.
    """
    suffix = os.path.splitext(filename)[1] or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        md = MarkItDown()
        result = md.convert(tmp_path)
        text = result.text_content.strip()
        logger.info(f"markitdown: {len(text)} caracteres extraídos")

        # Fallback a OCR si markitdown no extrajo texto (PDF escaneado)
        if not text and suffix == ".pdf":
            logger.info("Texto vacío — activando OCR...")
            text = ocr_pdf(tmp_path)
            logger.info(f"OCR: {len(text)} caracteres extraídos")
    except Exception:
        raise
    finally:
        os.unlink(tmp_path)

    return text


def extract_subsidies(text: str) -> list:
    """Llama a Claude Haiku con el texto del documento y devuelve la lista
    de subsidios como objetos Python. Claude devuelve solo la estructura de datos;
    la construcción del JSON final la hace Python con plantillas fijas.
    """
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": f"{EXTRACT_PROMPT}\n\nDocumento:\n{text}"}]
    )
    raw = response.content[0].text.strip()
    # Claude a veces envuelve la respuesta en bloques markdown ```json ... ```
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
