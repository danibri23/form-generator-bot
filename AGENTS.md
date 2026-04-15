# form-generator-bot — AGENTS.md

Guía para agentes de IA que trabajen en este proyecto.

## Qué hace este proyecto

Bot de Slack que recibe documentos de servicios de solidaridad de cooperativas (PDF, DOCX, PPTX, XLSX) y genera automáticamente el JSON de formulario dinámico para la app móvil de BrosCo.

**Flujo completo:**
1. Usuario sube un archivo al canal de Slack
2. Bot descarga y extrae el texto (markitdown → OCR como fallback)
3. Claude Haiku analiza el texto y devuelve solo la estructura de datos (lista de subsidios)
4. Python construye el JSON final usando plantillas fijas
5. Bot sube el `.json` resultante al mismo canal

## Estructura del proyecto

```
form-generator-bot/
├── bot.py              # Código principal del bot
├── schema_example.json # Ejemplo de JSON de salida esperado (referencia)
├── requirements.txt    # Dependencias Python
├── .env                # Variables de entorno (no commitear)
└── AGENTS.md           # Este archivo
```

## Variables de entorno (.env)

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...
```

## Dependencias del sistema

Además de `requirements.txt`, se requieren herramientas del sistema:
- `brew install poppler` — para convertir PDFs a imágenes (usado por pdf2image en OCR)
- `brew install tesseract tesseract-lang` — para OCR de PDFs escaneados

## Cómo correr el bot

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Arquitectura de `bot.py`

| Función | Responsabilidad |
|---|---|
| `file_to_text()` | Extrae texto del archivo (markitdown + fallback OCR) |
| `ocr_pdf()` | OCR con pytesseract para PDFs escaneados |
| `extract_subsidies()` | Llama a Claude Haiku y parsea la respuesta JSON |
| `build_json()` | Construye el JSON completo del formulario con plantillas fijas |
| `make_photo_widget()` | Plantilla para widget de subida de documentos |
| `make_ci_widgets()` | Plantilla fija para frente y dorso de cédula de identidad |
| `get_icon()` | Resuelve el icono S3 según la categoría del subsidio |
| `handle_file_shared()` | Handler de Slack: orquesta todo el flujo |

## Estructura del JSON de salida

El JSON generado sigue el schema de formularios dinámicos de BrosCo:

```json
{
  "type": "subsidy",
  "showProgress": true,
  "helpButton": { ... },
  "pages": [
    { "widgets": [heading, subheading, radio] },         // página 0: selección
    { "widgets": [...], "visibilityConditions": [...] }, // una por subsidio
    { "widgets": [heading, image, subheading] }          // página final: éxito
  ]
}
```

**Convenciones de claves:**
- `key` de widgets → **camelCase** (ej: `subsidioIncapacidadTotal`)
- `value` del radio y `comparisonValue` de visibilityConditions → **kebab-case** (ej: `subsidio-incapacidad-total`)

## Reglas críticas al modificar el código

1. **No agregar validación `file-type` en widgets `photo`** — causa crash en Flutter (`ImageObject is not a subtype of FileObject`). Solo usar `required`.

2. **No modificar `make_ci_widgets()`** sin coordinar con el equipo móvil — los `key` `memberDocumentFrontPhoto` y `memberDocumentBackPhoto` son fijos y los consume el backend.

3. **La página "¡Terminamos!"** en `build_json()` no tiene `visibilityConditions` — debe estar siempre presente y ser la última página.

4. **Claude solo extrae datos, Python construye el JSON** — no pedirle a Claude que genere el JSON final, es costoso y propenso a errores de formato.

5. **Separar subsidios por beneficiario** — nunca agrupar socio/cónyuge/hijo/padre en un mismo item. Son items separados. Excepción: hijo/hija se tratan como uno, padre/madre se tratan como uno.

## Orden de categorías

Los subsidios siempre aparecen en este orden en el radio:
1. nacimiento
2. matrimonio
3. estudio
4. medico / salud
5. accidente
6. incendio
7. fallecimiento

Definido en `CATEGORY_ORDER` en `bot.py`.

## Modelo de Claude usado

`claude-haiku-4-5-20251001` — modelo económico, suficiente para extracción estructurada de datos.
No cambiar a modelos más caros sin analizar el volumen de uso.
