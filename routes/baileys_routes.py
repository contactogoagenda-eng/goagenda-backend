from fastapi import APIRouter
from pydantic import BaseModel

from services.ai_agent import procesar_mensaje
from services.db import get_business_by_id

router = APIRouter()

# Historial en memoria por negocio + cliente
# (igual que en webhook.py para Meta)
historiales_baileys = {}


class BaileysMessageInput(BaseModel):
    business_id: str
    client_phone: str
    mensaje: str


@router.post("/baileys/message")
def recibir_mensaje_baileys(data: BaileysMessageInput):
    """
    Recibe mensajes desde el servicio de Baileys (WhatsApp no oficial).
    Funciona igual que el webhook de Meta, pero el business_id llega
    directamente en el payload (Baileys sabe a cual negocio pertenece
    porque cada instancia tiene su propio BUSINESS_ID en el .env).
    """
    business = get_business_by_id(data.business_id)
    if not business:
        return {"error": "Negocio no encontrado"}

    print(f"[Baileys][{business['name']}] Mensaje de {data.client_phone}: {data.mensaje}")

    key = f"{data.business_id}:{data.client_phone}"
    historial_previo = historiales_baileys.get(key, [])

    respuesta, nuevo_historial = procesar_mensaje(
        mensaje_cliente=data.mensaje,
        business_id=data.business_id,
        client_phone=data.client_phone,
        historial=historial_previo,
    )

    historiales_baileys[key] = nuevo_historial

    # Retorna la respuesta — Baileys la enviará al cliente
    return {"respuesta_ia": respuesta}