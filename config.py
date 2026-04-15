"""
config.py
=========
Constantes compartidas entre los módulos del bot.
"""

# URL base de los assets en S3 para iconos e imágenes de la app.
ASSETS_BASE = "https://brosco-assets.s3.us-east-2.amazonaws.com/mobile"

# Mapeo de palabras clave del campo "category" al nombre del icono en S3.
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

# Prompt para Claude Haiku. Claude devuelve solo la estructura de datos;
# Python construye el JSON final con plantillas fijas.
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
