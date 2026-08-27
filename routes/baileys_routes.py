import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from services.auth import obtener_usuario_actual, verificar_dueno, requiere_api_key_interna
from services.baileys_client import solicitar_pairing_code, estado_conexion
from services.db import get_business_by_id

router = APIRouter(tags=["whatsapp"])


class BaileysMessageInput(BaseModel):
    business_id: str
    client_phone: str
    mensaje: str


@router.post("/baileys/message", dependencies=[Depends(requiere_api_key_interna)])
def recibir_mensaje_baileys(data: BaileysMessageInput):
    """
    El bot de IA conversacional ya NO responde por WhatsApp (se movio al
    chat web publico, ver routes/chat_routes.py). Este endpoint se deja
    activo (respondiendo siempre respuesta_ia=null) para no romper al
    baileys-service si todavia lo llama; se puede borrar cuando se
    confirme que ya no lo usa.
    """
    business = get_business_by_id(data.business_id)
    if not business:
        return {"error": "Negocio no encontrado"}

    print(f"[Baileys][{business['name']}] Mensaje de {data.client_phone} (IA desactivada en WhatsApp): {data.mensaje}")
    return {"respuesta_ia": None}


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