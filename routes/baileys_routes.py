import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from services.ai_agent import procesar_mensaje
from services.auth import obtener_usuario_actual, verificar_dueno, requiere_api_key_interna
from services.baileys_client import solicitar_pairing_code, estado_conexion
from services.db import get_business_by_id

router = APIRouter()

# Historial en memoria por negocio + cliente
# Cada entrada guarda: {"historial": [...], "ultima_actividad": "iso_timestamp"}
# (igual que en webhook.py para Meta)
historiales_baileys = {}


class BaileysMessageInput(BaseModel):
    business_id: str
    client_phone: str
    mensaje: str


@router.post("/baileys/message", dependencies=[Depends(requiere_api_key_interna)])
def recibir_mensaje_baileys(data: BaileysMessageInput):
    """
    Recibe mensajes desde el servicio de Baileys (WhatsApp no oficial).
    Funciona igual que el webhook de Meta, pero el business_id llega
    directamente en el payload. Solo el baileys-service puede llamarlo
    (exige el header x-api-key con la key interna compartida).
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
def solicitar_codigo_vinculacion(data: PairingCodeInput, user_id: str = Depends(obtener_usuario_actual)):
    """
    Pide al servicio de Baileys un codigo de vinculacion de 8 caracteres
    para que el dueño del negocio conecte su WhatsApp desde su propio
    celular (WhatsApp → Dispositivos vinculados → Vincular con el numero
    de telefono), sin necesidad de escanear un QR en otra pantalla.
    La app Flutter llama este endpoint; la API key interna de Baileys
    nunca sale del backend.
    """
    verificar_dueno(data.business_id, user_id)

    telefono = "".join(c for c in data.phone if c.isdigit())
    if len(telefono) < 10:
        return {"error": "Numero invalido: debe incluir el indicativo del pais, ej: 573001234567"}

    try:
        return solicitar_pairing_code(data.business_id, telefono)
    except requests.exceptions.RequestException as e:
        print(f"Error pidiendo codigo de vinculacion a Baileys: {e}")
        return {"error": "No se pudo contactar el servicio de WhatsApp. Intenta de nuevo en un momento."}


@router.get("/baileys/status")
def estado_conexion_baileys(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    Consulta si el WhatsApp de un negocio esta conectado via Baileys.
    Pensado para que la app muestre el estado en la pantalla de vinculacion.
    """
    verificar_dueno(business_id, user_id)
    try:
        return estado_conexion(business_id)
    except requests.exceptions.RequestException as e:
        print(f"Error consultando estado de Baileys: {e}")
        return {"error": "No se pudo contactar el servicio de WhatsApp."}