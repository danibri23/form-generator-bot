import os
import json
import tempfile
import requests
import pdfplumber
import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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

ASSETS_BASE = "https://brosco-assets.s3.us-east-2.amazonaws.com/mobile"

EXTRACT_PROMPT = """Analizá el siguiente documento de servicios de solidaridad de una cooperativa.
Extraé cada subsidio/premio y devolvé ÚNICAMENTE un JSON con esta estructura (sin markdown, sin explicaciones):

[
  {
    "key": "slug-unico",
    "label": "Nombre del subsidio",
    "description": "Monto o descripción corta",
    "category": "medico|fallecimiento|nacimiento|matrimonio|estudio|incendio",
    "documents": [
      {"key": "keyUnico", "text": "Nombre del documento", "description": "Detalle breve"}
    ],
    "requires_ci": true
  }
]

Reglas:
- key: slug en minúsculas con guiones
- requires_ci: true si piden cédula de identidad
- documents: solo los documentos específicos del subsidio (no incluyas la cédula, esa se agrega automáticamente)
- Sé conciso en los textos"""


def pdf_to_text(file_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    text = ""
    with pdfplumber.open(tmp_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""

    os.unlink(tmp_path)
    return text.strip()


def extract_subsidies(text: str) -> list:
    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": f"{EXTRACT_PROMPT}\n\nDocumento:\n{text}"}]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    print("=== RESPUESTA CLAUDE ===")
    print(raw[:500])
    print("=======================")
    return json.loads(raw.strip())


def get_icon(category: str) -> str:
    for keyword, icon in ICON_MAP.items():
        if keyword in category.lower():
            return f"{ASSETS_BASE}/{icon}.png"
    return f"{ASSETS_BASE}/document.png"


def make_photo_widget(key: str, text: str, description: str, src: str) -> dict:
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


def build_json(subsidies: list) -> dict:
    # Radio options
    options = []
    for s in subsidies:
        options.append({
            "value": s["key"],
            "label": s["label"],
            "description": s["description"],
            "src": get_icon(s.get("category", ""))
        })

    pages = [
        {
            "widgets": [
                {"type": "heading", "key": "titlePage", "widgetData": {"text": "Servicios de Solidaridad"}},
                {"type": "subheading", "key": "subtitlePage", "widgetData": {"text": "Seleccioná el servicio al que querés acceder."}},
                {
                    "type": "radio",
                    "key": "subsidyType",
                    "widgetData": {"label": "Seleccioná el tipo de servicio:", "options": options, "disclaimer": ""},
                    "visibilityConditions": [],
                    "validations": [{"type": "required", "errorMessage": "Debes seleccionar un tipo de servicio."}]
                }
            ]
        }
    ]

    # One page per subsidy
    for s in subsidies:
        key = s["key"]
        widgets = [
            {"type": "heading", "key": f"title_{key}", "widgetData": {"text": s["label"]}},
            {"type": "subheading", "key": f"subtitle_{key}", "widgetData": {"text": s["description"]}},
        ]

        for doc in s.get("documents", []):
            widgets.append(make_photo_widget(
                key=doc["key"],
                text=doc["text"],
                description=doc.get("description", "Asegúrate que los datos sean legibles"),
                src=get_icon(s.get("category", ""))
            ))

        if s.get("requires_ci", True):
            widgets.extend(make_ci_widgets())

        pages.append({
            "widgets": widgets,
            "visibilityConditions": [{"inputKey": "subsidyType", "operator": "==", "comparisonValue": key}]
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


@app.event("file_shared")
def handle_file_shared(event, client, say):
    file_id = event.get("file_id")
    channel_id = event.get("channel_id")

    file_info = client.files_info(file=file_id)
    file = file_info["file"]

    if not file["name"].lower().endswith(".pdf"):
        say(channel=channel_id, text="Por favor subí un archivo PDF con los requisitos.")
        return

    say(channel=channel_id, text="Recibí el PDF, procesando... un momento")

    file_url = file["url_private_download"]
    headers = {"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"}
    response = requests.get(file_url, headers=headers)

    try:
        text = pdf_to_text(response.content)
    except Exception as e:
        say(channel=channel_id, text=f"Error al leer el PDF: {e}")
        return

    if not text:
        say(channel=channel_id, text="No pude extraer texto del PDF. Asegurate que no sea una imagen escaneada.")
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
