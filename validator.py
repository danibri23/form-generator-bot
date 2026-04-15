"""
validator.py
============
Validación del JSON generado antes de subirlo al canal de Slack.
Detecta errores estructurales que romperían el formulario en la app.
"""


def validate_json(result: dict) -> list[str]:
    """Valida el JSON del formulario dinámico y devuelve una lista de errores.
    Si la lista está vacía, el JSON es válido.

    Validaciones:
      1. Al menos 1 subsidio (mínimo 3 páginas: radio + 1 subsidio + éxito)
      2. Ninguna página con widgets vacíos
      3. Keys de widgets únicos globalmente
      4. Cada value del radio tiene su página con visibilityCondition correspondiente
      5. Ningún key de widget contiene guiones o espacios (indica fallo en to_camel_case)
    """
    errors = []
    pages = result.get("pages", [])

    # 1. Al menos 1 subsidio
    if len(pages) < 3:
        errors.append(f"Se esperaban al menos 3 páginas (radio + 1 subsidio + éxito), se encontraron {len(pages)}.")

    # 2. Sin páginas vacías
    for i, page in enumerate(pages):
        if not page.get("widgets"):
            errors.append(f"La página {i} no tiene widgets.")

    # 3. Keys únicos globalmente
    all_keys = []
    for page in pages:
        for widget in page.get("widgets", []):
            key = widget.get("key")
            if key:
                all_keys.append(key)

    seen = set()
    duplicates = set()
    for key in all_keys:
        if key in seen:
            duplicates.add(key)
        seen.add(key)

    # Estas keys se repiten intencionalmente en múltiples páginas — no son errores:
    # - CI keys: una por cada página de subsidio
    # - titlePage/subtitlePage: aparecen en la página del radio y en la página de éxito
    static_keys = {"memberDocumentFrontPhoto", "memberDocumentBackPhoto", "titlePage", "subtitlePage"}
    real_duplicates = duplicates - static_keys
    if real_duplicates:
        errors.append(f"Keys duplicados encontrados: {', '.join(sorted(real_duplicates))}")

    # 4. Cada value del radio tiene su página correspondiente
    radio_values = set()
    for page in pages:
        for widget in page.get("widgets", []):
            if widget.get("type") == "radio" and widget.get("key") == "subsidyType":
                for option in widget.get("widgetData", {}).get("options", []):
                    radio_values.add(option["value"])

    page_slugs = set()
    for page in pages:
        for condition in page.get("visibilityConditions", []):
            if condition.get("inputKey") == "subsidyType":
                page_slugs.add(condition["comparisonValue"])

    missing_pages = radio_values - page_slugs
    if missing_pages:
        errors.append(f"Opciones del radio sin página correspondiente: {', '.join(sorted(missing_pages))}")

    # 5. Ningún key con guiones o espacios (camelCase mal generado)
    bad_keys = [k for k in all_keys if "-" in k or " " in k]
    if bad_keys:
        errors.append(f"Keys con formato incorrecto (deben ser camelCase): {', '.join(bad_keys)}")

    return errors
