import os
from fastapi import APIRouter, Request, Query
from dotenv import load_dotenv

from services.whatsapp import send_whatsapp_message
from services.ai_agent import procesar_mensaje
from services.db import get_business_by_whatsapp_phone_id

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

router = APIRouter()

# Historial en memoria, separado por negocio + numero de cliente.
# Cada entrada guarda: {"historial": [...], "ultima_actividad": "iso_timestamp"}
# (se pierde si el backend se reinicia; para produccion real con varios
# negocios activos, considerar persistirlo en Supabase mas adelante)
historiales_whatsapp = {}


@router.get("/webhook")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=None),
    hub_verify_token: str = Query(alias="hub.verify_token", default=None),
    hub_challenge: str = Query(alias="hub.challenge", default=None),
):
    """Meta llama este endpoint para verificar que el webhook es valido."""
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)
    return {"error": "Verificacion fallida"}


@router.post("/webhook")
async def receive_message(request: Request):
    """
    Recibe los mensajes entrantes de WhatsApp de CUALQUIER negocio conectado.
    Identifica el negocio dinamicamente segun el phone_number_id que llega
    en cada mensaje (metadata.phone_number_id), en vez de asumir un solo
    negocio fijo. Esto permite tener varios negocios, cada uno con su propio
    numero de WhatsApp, todos apuntando al mismo backend y webhook.
    """
    body = await request.json()
    print("Webhook recibido:", body)

    try:
        entry = body["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        # Los eventos de "statuses" (entregado/leido) no son mensajes nuevos, se ignoran
        if "messages" not in value:
            return {"status": "ok"}

        # phone_number_id identifica a CUAL numero (y por lo tanto, a cual negocio)
        # le llego este mensaje. Es distinto del numero del cliente que escribe.
        phone_number_id = value["metadata"]["phone_number_id"]

        business = get_business_by_whatsapp_phone_id(phone_number_id)
        if business is None:
            print(f"Mensaje recibido para un phone_number_id no registrado en ningun negocio: {phone_number_id}")
            return {"status": "ok"}

        business_id = business["id"]

        message = value["messages"][0]
        from_number = message["from"]
        text = message.get("text", {}).get("body", "")

        if not text:
            print("Mensaje sin texto (audio/imagen/sticker), se ignora por ahora.")
            return {"status": "ok"}

        print(f"[{business['name']}] Mensaje de {from_number}: {text}")

        key = f"{business_id}:{from_number}"
        estado_previo = historiales_whatsapp.get(key, {})
        historial_previo = estado_previo.get("historial", [])
        ultima_actividad_previa = estado_previo.get("ultima_actividad")

        respuesta, nuevo_historial, nueva_ultima_actividad = procesar_mensaje(
            mensaje_cliente=text,
            business_id=business_id,
            client_phone=from_number,
            historial=historial_previo,
            ultima_actividad=ultima_actividad_previa,
        )

        historiales_whatsapp[key] = {
            "historial": nuevo_historial,
            "ultima_actividad": nueva_ultima_actividad,
        }

        if respuesta is not None:
            send_whatsapp_message(to=from_number, text=respuesta, business_phone_number_id=phone_number_id)

    except (KeyError, IndexError) as e:
        print("No es un mensaje de texto entrante o el payload no tiene el formato esperado:", e)

    return {"status": "ok"}