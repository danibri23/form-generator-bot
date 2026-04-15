"""
builder.py
==========
Construcción del JSON de formulario dinámico a partir de la lista
de subsidios extraídos por Claude.
"""

import re
from config import ASSETS_BASE, ICON_MAP, CATEGORY_ORDER


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
                key=to_camel_case(f"{key}-{doc['key']}"),
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
