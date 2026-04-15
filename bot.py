"""
form-generator-bot
==================
Bot de Slack que recibe documentos de servicios de solidaridad (PDF, DOCX, etc.)
y genera automáticamente el JSON de formulario dinámico para la app móvil de BrosCo.

Flujo:
  1. El usuario sube un archivo al canal de Slack.
  2. El bot extrae el texto (via markitdown; OCR como fallback para PDFs escaneados).
  3. Claude Haiku analiza el texto y extrae la lista de subsidios estructurada.
  4. Python construye el JSON final usando plantillas fijas.
  5. El bot sube el JSON resultante al mismo canal.

Variables de entorno requeridas (.env):
  SLACK_BOT_TOKEN   — token del bot de Slack (xoxb-...)
  SLACK_APP_TOKEN   — token de la app en modo socket (xapp-...)
  ANTHROPIC_API_KEY — clave de API de Anthropic
"""

import os
import re
import json
import tempfile
import requests
from markitdown import MarkItDown
import pytesseract
from pdf2image import convert_from_path
import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ---------------------------------------------------------------------------
# Configuración de iconos y assets
# ---------------------------------------------------------------------------

# Mapeo de palabras clave del campo "category" al nombre del icono en S3.
# Se usa como imagen representativa en el widget radio y en los widgets photo.
ICON_MAP = {
    "medico": "medical",
    "salud": "medical",
    "internacion": "medical",
    "incapacidad": "medical",
    "accidente": "medical",
    "analisis": "medical",
    "fallecimiento": "death",
    "defuncion": "death",
    "nacimiento": "born",
    "adopcion": "born",
    "matrimonio": "wedding",
    "estudio": "college-degree",
    "titulo": "college-degree",
    "universitario": "college-degree",
    "incendio": "document",
}

# URL base de los assets en S3 para iconos e imágenes de la app.
ASSETS_BASE = "https://brosco-assets.s3.us-east-2.amazonaws.com/mobile"

# Orden en el que aparecen los subsidios en el radio de selección.
CATEGORY_ORDER = [
    "nacimiento",
    "matrimonio",
    "estudio",
    "medico",
    "salud",
    "accidente",
    "incendio",
    "fallecimiento",
]

# ---------------------------------------------------------------------------
# Prompt para Claude Haiku
# ---------------------------------------------------------------------------

# Claude recibe el texto del documento y devuelve un JSON con los subsidios.
# Python se encarga de construir el JSON final con las plantillas fijas,
# evitando que Claude genere JSONs largos y costosos.
EXTRACT_PROMPT = """Analizá el siguiente documento de servicios de solidaridad de una cooperativa.
Extraé cada subsidio/premio y devolvé ÚNICAMENTE un JSON con esta estructura (sin markdown, sin explicaciones):

[
  {
    "key": "slug-unico",
    "label": "Nombre del subsidio",
    "description": "Monto o descripción corta",
    "category": "medico|fallecimiento|nacimiento|matrimonio|estudio|incendio",
    "intro": "Mensaje breve de contexto o felicitación para el usuario (ej: ¡Felicidades! Subí los siguientes documentos para acceder al subsidio.)",
    "items": ["Texto del requisito 1", "Texto del requisito 2"],
    "documents": [
      {"key": "keyUnico", "text": "Nombre del documento", "description": "Detalle breve"}
    ],
    "requires_ci": true
  }
]

Reglas:
- key: camelCase sin espacios, guiones ni guion bajo (ej: subsidioIncapacidadTotal)
- requires_ci: true si piden cédula de identidad
- documents: solo los documentos específicos del subsidio (no incluyas la cédula, esa se agrega automáticamente)
- Sé conciso en los textos
- La "description" de cada documento debe tener máximo 60 caracteres, clara y directa
- IMPORTANTE: si un subsidio aplica a distintos beneficiarios, creá un item SEPARADO por cada uno. Los beneficiarios son: socio titular, cónyuge, hijo/hija (se tratan como uno solo), padre/madre (se tratan como uno solo). NUNCA agrupés beneficiarios distintos en un mismo item. Ej: subsidioAccidentesSocio, subsidioAccidentesConyuge, subsidioAccidentesHijo."""


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def to_camel_case(text: str) -> str:
    """Convierte un slug (kebab-case o snake_case) a camelCase.
    Ejemplo: 'subsidio-por-nacimiento' → 'subsidioPorNacimiento'
    """
    parts = text.replace("-", " ").replace("_", " ").split()
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:])


def to_kebab_case(text: str) -> str:
    """Convierte camelCase o cualquier texto a kebab-case.
    Ejemplo: 'subsidioPorNacimiento' → 'subsidio-por-nacimiento'
    """
    text = re.sub(r'([A-Z])', r'-\1', text).lower().strip('-')
    return re.sub(r'[-_\s]+', '-', text)


# ---------------------------------------------------------------------------
# Extracción de texto del archivo
# ---------------------------------------------------------------------------

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

        # Fallback a OCR si markitdown no extrajo texto (PDF escaneado)
        if not text and suffix == ".pdf":
            text = ocr_pdf(tmp_path)
    except Exception:
        raise
    finally:
        os.unlink(tmp_path)

    return text


# ---------------------------------------------------------------------------
# Extracción de subsidios con Claude Haiku
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Construcción del JSON de formulario
# ---------------------------------------------------------------------------

def get_icon(category: str) -> str:
    """Devuelve la URL del icono correspondiente a la categoría del subsidio."""
    for keyword, icon in ICON_MAP.items():
        if keyword in category.lower():
            return f"{ASSETS_BASE}/{icon}.png"
    return f"{ASSETS_BASE}/document.png"


def make_photo_widget(key: str, text: str, description: str, src: str) -> dict:
    """Genera un widget de tipo photo para subir un documento.
    Permite múltiples archivos y tanto cámara como galería.
    IMPORTANTE: no incluir validación 'file-type' — causa crash en Flutter
    al intentar castear ImageObject a FileObject.
    """
    return {
        "type": "photo",
        "key": key,
        "widgetData": {
            "label": "Subí un archivo",
            "text": text,
            "description": description,
            "src": src,
            "multiple": True,
            "camera": "both",
            "allowFromMedia": True,
            "disclaimer": ""
        },
        "visibilityConditions": [],
        "validations": [{"type": "required", "errorMessage": f"Debes subir {text.lower()}."}]
    }


def make_ci_widgets() -> list:
    """Genera los dos widgets fijos para fotografiar la cédula de identidad
    (frente y dorso). Se agregan al final de cada subsidio que requiere CI.
    """
    return [
        {
            "type": "photo",
            "key": "memberDocumentFrontPhoto",
            "widgetData": {
                "label": "Tomá una foto",
                "text": "Frente de tu cédula de identidad",
                "description": "Asegúrate que los datos sean legibles",
                "src": f"{ASSETS_BASE}/ci-front.png",
                "camera": "both",
                "multiple": False,
                "allowFromMedia": False,
                "guidingShape": "",
                "disclaimer": ""
            },
            "visibilityConditions": [],
            "validations": [{"type": "required", "errorMessage": "Debes subir una foto del frente de tu cédula."}]
        },
        {
            "type": "photo",
            "key": "memberDocumentBackPhoto",
            "widgetData": {
                "label": "Tomá una foto",
                "text": "Dorso de tu cédula de identidad",
                "description": "Asegúrate que los datos sean legibles",
                "src": f"{ASSETS_BASE}/ci-back.png",
                "camera": "both",
                "multiple": False,
                "allowFromMedia": False,
                "guidingShape": "",
                "disclaimer": ""
            },
            "visibilityConditions": [],
            "validations": [{"type": "required", "errorMessage": "Debes subir una foto del dorso de tu cédula."}]
        }
    ]


def category_priority(s: dict) -> int:
    """Devuelve el índice de prioridad del subsidio según CATEGORY_ORDER.
    Los subsidios sin categoría reconocida van al final.
    """
    cat = s.get("category", "").lower()
    for i, keyword in enumerate(CATEGORY_ORDER):
        if keyword in cat:
            return i
    return len(CATEGORY_ORDER)


def build_json(subsidies: list) -> dict:
    """Construye el JSON completo del formulario dinámico a partir de la lista
    de subsidios extraídos por Claude.

    Estructura del JSON resultante:
      - Página 0: radio de selección de tipo de subsidio (con todos los options)
      - Páginas 1..N: una página por subsidio, con visibilityCondition al radio
      - Última página: pantalla de éxito "¡Terminamos!"
    """
    subsidies = sorted(subsidies, key=category_priority)

    # Opciones del radio de selección (página inicial)
    options = []
    for s in subsidies:
        options.append({
            "value": to_kebab_case(s["key"]),
            "label": s["label"],
            "description": s["description"],
            "src": get_icon(s.get("category", ""))
        })

    pages = [
        {
            "widgets": [
                {"type": "heading", "key": "titlePage", "widgetData": {"text": "Formulario de solicitud de subsidios"}},
                {"type": "subheading", "key": "subtitlePage", "widgetData": {"text": "Ante ciertas situaciones, tu cooperativa te respalda mediante los siguientes tipos de subsidios."}},
                {
                    "type": "radio",
                    "key": "subsidyType",
                    "widgetData": {"label": "Seleccioná el tipo de subsidio:", "options": options, "disclaimer": ""},
                    "visibilityConditions": [],
                    "validations": [{"type": "required", "errorMessage": "Debes seleccionar un tipo de subsidio."}]
                }
            ]
        }
    ]

    # Una página por subsidio, visible solo cuando el radio coincide
    for s in subsidies:
        slug = to_kebab_case(s["key"])
        key = to_camel_case(slug)
        widgets = [
            {"type": "heading", "key": f"title{key.capitalize()}", "widgetData": {"text": s["label"]}},
        ]

        if s.get("intro"):
            widgets.append({"type": "subheading", "key": f"subtitle{key.capitalize()}", "widgetData": {"text": s["intro"]}})

        if s.get("items"):
            widgets.append({"type": "subheading", "key": f"subtitle2{key.capitalize()}", "widgetData": {"text": "Para acceder al subsidio debes:"}})
            widgets.append({"type": "list", "key": f"list{key.capitalize()}", "widgetData": {"items": s["items"]}})

        for doc in s.get("documents", []):
            widgets.append(make_photo_widget(
                key=to_camel_case(doc["key"]),
                text=doc["text"],
                description=doc.get("description", "Asegúrate que los datos sean legibles"),
                src=get_icon(s.get("category", ""))
            ))

        if s.get("requires_ci", True):
            widgets.extend(make_ci_widgets())

        pages.append({
            "widgets": widgets,
            "visibilityConditions": [{"inputKey": "subsidyType", "operator": "==", "comparisonValue": slug}]
        })

    # Página final de confirmación (siempre presente, sin visibilityConditions)
    pages.append({
        "widgets": [
            {
                "type": "heading",
                "key": "titlePage",
                "widgetData": {"text": "¡Terminamos!"}
            },
            {
                "type": "image",
                "key": "successImage",
                "widgetData": {"src": "https://brosco-assets.s3.us-east-2.amazonaws.com/mobile/ok.png"}
            },
            {
                "type": "subheading",
                "key": "subtitlePage",
                "widgetData": {"text": "Hemos recibido tu solicitud de subsidio. La revisaremos y te notificaremos vía correo electrónico sobre la resolución muy pronto."}
            }
        ]
    })

    return {
        "type": "subsidy",
        "showProgress": True,
        "helpButton": {
            "backgroundColor": "3192EC",
            "textColor": "37404F",
            "text": "Necesito ayuda",
            "action": {"type": "whatsapp", "phone": "", "message": "Hola, necesito ayuda con mi solicitud.", "attachUserInfo": True}
        },
        "pages": pages
    }


# ---------------------------------------------------------------------------
# Handler de Slack
# ---------------------------------------------------------------------------

@app.event("file_shared")
def handle_file_shared(event, client, say):
    """Maneja el evento de archivo compartido en Slack.
    Descarga el archivo, extrae el texto, llama a Claude y sube el JSON generado.
    """
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")

    file_info = client.files_info(file=file_id)
    file = file_info["file"]

    SUPPORTED = (".pdf", ".docx", ".doc", ".pptx", ".xlsx")
    if not any(file["name"].lower().endswith(ext) for ext in SUPPORTED):
        say(channel=channel_id, text=f"Formato no soportado. Subí un archivo: {', '.join(SUPPORTED)}")
        return

    say(channel=channel_id, text="Recibí el archivo, procesando... un momento")

    file_url = file["url_private_download"]
    headers = {"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}
    response = requests.get(file_url, headers=headers)

    try:
        text = file_to_text(response.content, file["name"])
    except Exception as e:
        say(channel=channel_id, text=f"Error al leer el archivo: {e}")
        return

    if not text:
        say(channel=channel_id, text="No pude extraer texto del archivo.")
        return

    try:
        subsidies = extract_subsidies(text)
        result = build_json(subsidies)
        result_json = json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        say(channel=channel_id, text=f"Error al generar el JSON: {e}")
        return

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
        tmp.write(result_json)
        tmp_path = tmp.name

    client.files_upload_v2(
        channel=channel_id,
        file=tmp_path,
        filename=f"{file['name'].replace('.pdf', '')}.json",
        initial_comment="JSON generado exitosamente!"
    )

    os.unlink(tmp_path)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
