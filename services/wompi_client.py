"""
Cliente HTTP de la API de Wompi (pasarela de pagos).

Se usa el flujo de "Links de pago" (`/v1/payment_links`) en vez de crear
transacciones directas (`/v1/transactions`): un link de pago genera una URL
de checkout (https://checkout.wompi.co/l/<id>) hospedada por Wompi, que el
bot puede mandar tal cual dentro de un mensaje de texto del chat - no hace
falta un formulario de pago embebido, ni tokens de aceptacion, ni firma de
integridad de cada transaccion (eso solo aplica al endpoint /transactions).
Ver https://docs.wompi.co/docs/colombia/links-de-pago/.

Cada negocio tiene sus propias llaves (multi-tenant, ver
services/wompi_credentials.py) — este modulo nunca lee credenciales de
variables de entorno, siempre las recibe como argumento ya desencriptadas.
"""

from datetime import datetime, timedelta, timezone

import httpx

SANDBOX_BASE_URL = "https://sandbox.wompi.co/v1"
PRODUCTION_BASE_URL = "https://production.wompi.co/v1"

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class WompiError(Exception):
    """Error al llamar a la API de Wompi (credenciales invalidas, request mal formado, etc)."""

    def __init__(self, mensaje: str, status_code: int | None = None):
        super().__init__(mensaje)
        self.status_code = status_code


def _base_url(sandbox_mode: bool) -> str:
    return SANDBOX_BASE_URL if sandbox_mode else PRODUCTION_BASE_URL


def obtener_info_comercio(public_key: str, sandbox_mode: bool) -> dict:
    """
    Consulta los datos publicos del comercio asociado a una llave publica
    (GET /merchants/info). Se usa solo para VALIDAR que un par de
    credenciales es real y corresponde al ambiente (sandbox/produccion)
    seleccionado - no se necesita para crear links de pago.
    """
    try:
        respuesta = httpx.get(
            f"{_base_url(sandbox_mode)}/merchants/info",
            headers={"x-merchant-public-key": public_key},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise WompiError(f"No se pudo conectar con Wompi: {e}") from e

    if respuesta.status_code != 200:
        raise WompiError(
            f"Wompi rechazo la llave publica (HTTP {respuesta.status_code}). Verifica que sea correcta "
            f"y corresponda al ambiente seleccionado ({'Sandbox' if sandbox_mode else 'Produccion'}).",
            status_code=respuesta.status_code,
        )

    return respuesta.json().get("data", {})


def crear_link_de_pago(
    private_key: str,
    sandbox_mode: bool,
    nombre: str,
    descripcion: str,
    amount_in_cents: int,
    expira_en_horas: float | None = 24,
    redirect_url: str | None = None,
) -> dict:
    """
    Crea un link de pago de monto fijo y un solo uso (POST /payment_links).
    Devuelve el dict "data" de la respuesta de Wompi, que incluye "id" (para
    armar la URL de checkout: https://checkout.wompi.co/l/<id>) y el resto
    de campos del link creado.
    """
    payload = {
        "name": nombre[:255],
        "description": descripcion[:500] if descripcion else nombre[:500],
        "single_use": True,
        "collect_shipping": False,
        "currency": "COP",
        "amount_in_cents": amount_in_cents,
    }
    if redirect_url:
        payload["redirect_url"] = redirect_url
    if expira_en_horas:
        # La doc de Wompi dice "formato ISO 8601 con huso horario UTC (+5
        # horas que el horario colombiano)": el valor va en UTC tal cual
        # (esa aclaracion es solo informativa sobre la diferencia horaria,
        # NO una instruccion de restarle horas antes de enviarlo - un bug
        # anterior aqui SI restaba 5h, lo que mandaba una fecha ya pasada
        # y Wompi la rechazaba con 422 "Debe ser mayor a <ahora>").
        expira_en = datetime.now(timezone.utc) + timedelta(hours=expira_en_horas)
        payload["expires_at"] = expira_en.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        respuesta = httpx.post(
            f"{_base_url(sandbox_mode)}/payment_links",
            headers={"Authorization": f"Bearer {private_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise WompiError(f"No se pudo conectar con Wompi: {e}") from e

    if respuesta.status_code not in (200, 201):
        raise WompiError(
            f"Wompi rechazo la creacion del link de pago (HTTP {respuesta.status_code}): {respuesta.text[:300]}",
            status_code=respuesta.status_code,
        )

    return respuesta.json().get("data", {})


def construir_url_checkout(payment_link_id: str) -> str:
    """Arma la URL publica de checkout a partir del id devuelto por crear_link_de_pago."""
    return f"https://checkout.wompi.co/l/{payment_link_id}"


def consultar_transaccion(private_key: str, sandbox_mode: bool, transaction_id: str) -> dict:
    """
    Consulta el estado de una transaccion puntual (GET /transactions/{id}).
    Requiere la llave privada del negocio (las consultas sin auth o con
    llave publica ya no estan soportadas por Wompi). Se usa como respaldo
    del webhook (ej. un endpoint de "reintentar verificacion").
    """
    try:
        respuesta = httpx.get(
            f"{_base_url(sandbox_mode)}/transactions/{transaction_id}",
            headers={"Authorization": f"Bearer {private_key}"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise WompiError(f"No se pudo conectar con Wompi: {e}") from e

    if respuesta.status_code == 404:
        raise WompiError("Transaccion no encontrada en Wompi.", status_code=404)
    if respuesta.status_code != 200:
        raise WompiError(
            f"Wompi rechazo la consulta de transaccion (HTTP {respuesta.status_code}): {respuesta.text[:300]}",
            status_code=respuesta.status_code,
        )

    return respuesta.json().get("data", {})
