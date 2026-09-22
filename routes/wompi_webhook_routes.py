"""
Webhook de eventos de Wompi (ver https://docs.wompi.co/docs/colombia/eventos/).

Como cada negocio tiene su propia cuenta de Wompi (multi-tenant, ver
services/wompi_credentials.py), y en Wompi la URL de eventos se configura
UNA vez por cuenta/ambiente (no hay forma de que el payload identifique el
negocio por si solo), la URL que cada negocio debe registrar en su
Dashboard de Wompi (Configuracion > Eventos) incluye su propio business_id:
POST /wompi/webhooks/{business_id}. El panel (Ajustes > Pagos) le muestra
esta URL exacta al dueño para que la copie alla.
"""

import hashlib

from fastapi import APIRouter, HTTPException, Request

from services.wompi_credentials import obtener_credenciales_para_pago
from services.wompi_payment_requests import (
    actualizar_estado_transaccion,
    confirmar_cita_desde_pago,
    notificar_pago_confirmado,
    obtener_solicitud_por_payment_link,
)

router = APIRouter(prefix="/wompi", tags=["wompi"])


def _valor_por_ruta(data: dict, ruta: str):
    """Extrae un valor de un dict anidado a partir de una ruta tipo 'transaction.id' (usado por signature.properties)."""
    valor = data
    for parte in ruta.split("."):
        if not isinstance(valor, dict) or parte not in valor:
            return None
        valor = valor[parte]
    return valor


def _firma_valida(payload: dict, events_key: str) -> bool:
    """
    Verifica el checksum SHA256 de un evento de Wompi (algoritmo exacto de
    la doc, seccion Eventos > Seguridad): concatenar en orden los valores
    de signature.properties (leidos de data), el timestamp, y el secreto
    de eventos del negocio; el resultado debe ser igual a signature.checksum.
    Los properties son dinamicos por evento - nunca se asumen fijos.
    """
    firma = payload.get("signature") or {}
    properties = firma.get("properties") or []
    checksum_esperado = str(firma.get("checksum") or "").upper()
    timestamp = payload.get("timestamp")

    if not properties or not checksum_esperado or timestamp is None or not events_key:
        return False

    data = payload.get("data") or {}
    cadena = "".join(str(_valor_por_ruta(data, prop)) for prop in properties) + str(timestamp) + events_key
    checksum_calculado = hashlib.sha256(cadena.encode()).hexdigest().upper()
    return checksum_calculado == checksum_esperado


@router.post("/webhooks/{business_id}")
async def recibir_webhook_wompi(business_id: str, request: Request):
    """
    Recibe un evento de Wompi para este negocio. Siempre responde 200 salvo
    firma invalida o payload no parseable (los unicos casos donde vale la
    pena que Wompi reintente) - un negocio sin Wompi configurado, un evento
    de un tipo que no manejamos, o una transaccion que no viene de uno de
    nuestros links de pago, simplemente se ignoran en silencio.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload invalido")

    credenciales = obtener_credenciales_para_pago(business_id)
    if not credenciales:
        return {"received": True}

    if not _firma_valida(payload, credenciales["events_key"]):
        raise HTTPException(status_code=401, detail="Firma invalida")

    if payload.get("event") != "transaction.updated":
        return {"received": True}

    transaccion = (payload.get("data") or {}).get("transaction") or {}
    payment_link_id = transaccion.get("payment_link_id")
    transaction_id = transaccion.get("id")
    status = transaccion.get("status")

    if not payment_link_id or not transaction_id or not status:
        return {"received": True}

    solicitud = obtener_solicitud_por_payment_link(business_id, payment_link_id)
    if not solicitud:
        return {"received": True}

    status_anterior = solicitud.get("status")
    actualizar_estado_transaccion(solicitud["id"], transaction_id, status)

    if status == "APPROVED" and status_anterior != "paid":
        resultado_cita = confirmar_cita_desde_pago(business_id, solicitud)
        notificar_pago_confirmado(
            business_id,
            solicitud,
            transaccion.get("amount_in_cents"),
            cita=resultado_cita["cita"],
            conflicto=resultado_cita["conflicto"],
        )

    return {"received": True}
