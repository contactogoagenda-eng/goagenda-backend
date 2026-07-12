import os

import requests
from fastapi import APIRouter
from pydantic import BaseModel
from dotenv import load_dotenv

from services.ai_agent import procesar_mensaje
from services.db import get_business_by_id

load_dotenv()

# URL publica del servicio de Baileys y la API key interna que este exige
# (header x-api-key). Deben coincidir con el .env del baileys-service.
BAILEYS_SERVICE_URL = os.getenv("BAILEYS_SERVICE_URL", "http://localhost:3000")
BAILEYS_INTERNAL_API_KEY = os.getenv("BAILEYS_INTERNAL_API_KEY", "")

router = APIRouter()

# Historial en memoria por negocio + cliente
# Cada entrada guarda: {"historial": [...], "ultima_actividad": "iso_timestamp"}
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
    estado_previo = historiales_baileys.get(key, {})
    historial_previo = estado_previo.get("historial", [])
    ultima_actividad_previa = estado_previo.get("ultima_actividad")

    respuesta, nuevo_historial, nueva_ultima_actividad = procesar_mensaje(
        mensaje_cliente=data.mensaje,
        business_id=data.business_id,
        client_phone=data.client_phone,
        historial=historial_previo,
        ultima_actividad=ultima_actividad_previa,
    )

    historiales_baileys[key] = {
        "historial": nuevo_historial,
        "ultima_actividad": nueva_ultima_actividad,
    }

    # Retorna la respuesta — Baileys la enviará al cliente (None = no enviar nada)
    return {"respuesta_ia": respuesta}


class PairingCodeInput(BaseModel):
    business_id: str
    phone: str


@router.post("/baileys/pairing-code")
def solicitar_codigo_vinculacion(data: PairingCodeInput):
    """
    Pide al servicio de Baileys un codigo de vinculacion de 8 caracteres
    para que el dueño del negocio conecte su WhatsApp desde su propio
    celular (WhatsApp → Dispositivos vinculados → Vincular con el numero
    de telefono), sin necesidad de escanear un QR en otra pantalla.
    La app Flutter llama este endpoint; la API key interna de Baileys
    nunca sale del backend.
    """
    business = get_business_by_id(data.business_id)
    if not business:
        return {"error": "Negocio no encontrado"}

    telefono = "".join(c for c in data.phone if c.isdigit())
    if len(telefono) < 10:
        return {"error": "Numero invalido: debe incluir el indicativo del pais, ej: 573001234567"}

    try:
        respuesta = requests.post(
            f"{BAILEYS_SERVICE_URL}/pairing-code/{data.business_id}",
            json={"phone": telefono},
            headers={"x-api-key": BAILEYS_INTERNAL_API_KEY},
            timeout=30,
        )
        return respuesta.json()
    except requests.exceptions.RequestException as e:
        print(f"Error pidiendo codigo de vinculacion a Baileys: {e}")
        return {"error": "No se pudo contactar el servicio de WhatsApp. Intenta de nuevo en un momento."}


@router.get("/baileys/status")
def estado_conexion_baileys(business_id: str):
    """
    Consulta si el WhatsApp de un negocio esta conectado via Baileys.
    Pensado para que la app muestre el estado en la pantalla de vinculacion.
    """
    try:
        respuesta = requests.get(
            f"{BAILEYS_SERVICE_URL}/status/{business_id}",
            headers={"x-api-key": BAILEYS_INTERNAL_API_KEY},
            timeout=15,
        )
        return respuesta.json()
    except requests.exceptions.RequestException as e:
        print(f"Error consultando estado de Baileys: {e}")
        return {"error": "No se pudo contactar el servicio de WhatsApp."}