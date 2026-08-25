import os
from fastapi import APIRouter, Request, Query
from dotenv import load_dotenv

from services.db import get_business_by_whatsapp_phone_id

load_dotenv()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

router = APIRouter()


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
    negocio fijo.

    El bot de IA conversacional ya NO responde por WhatsApp (se movio al
    chat web publico, ver routes/chat_routes.py). Este endpoint sigue
    activo solo para: verificar el webhook ante Meta, y para el puente que
    confirma automaticamente una cita agendada en la web cuando el cliente
    escribe la frase de confirmacion por WhatsApp.
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

        # Interceptar el mensaje de confirmación desde la web
        if "Acabo de agendar una cita" in text and "a través de la web" in text:
            try:
                from services.db import supabase as client_supabase
                # Actualizamos a 'confirmed' cualquier cita pendiente de este cliente
                client_supabase.table("appointments").update({"status": "confirmed"}).eq("business_id", business_id).eq("client_phone", from_number).eq("status", "pending").execute()
                print(f"Cita web confirmada automaticamente via WhatsApp para {from_number}")
            except Exception as e:
                print("Error confirmando cita web automaticamente:", e)

    except (KeyError, IndexError) as e:
        print("No es un mensaje de texto entrante o el payload no tiene el formato esperado:", e)

    return {"status": "ok"}
from pydantic import BaseModel
from services.db import supabase as client_supabase

class NotifyWebBookingInput(BaseModel):
    appointment_id: str
    business_id: str

@router.post("/notify-web-booking")
def notify_web_booking(data: NotifyWebBookingInput):
    from services.push_notifications import enviar_notificacion_nueva_cita
    
    # 1. Obtener la cita
    res_apt = client_supabase.table("appointments").select("*, services(name), businesses(fcm_token)").eq("id", data.appointment_id).execute()
    if not res_apt.data:
        return {"error": "Cita no encontrada"}
        
    apt = res_apt.data[0]
    
    # Se elimina la actualizacion a 'confirmed' para que la cita
    # se mantenga en 'pending' hasta que el cliente escriba por WhatsApp.
        
    # 3. Enviar notificacion push
    fcm_token = apt.get("businesses", {}).get("fcm_token")
    if fcm_token:
        nombre_cliente = apt.get("client_name", "Cliente")
        nombre_servicio = apt.get("services", {}).get("name", "Servicio")
        
        # Formatear fecha y hora
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(apt.get("scheduled_at", ""))
            fecha_str = dt.strftime("%Y-%m-%d %H:%M")
        except:
            fecha_str = apt.get("scheduled_at", "una fecha")
            
        try:
            enviar_notificacion_nueva_cita(
                fcm_token=fcm_token,
                nombre_cliente=nombre_cliente,
                servicio=nombre_servicio,
                fecha_hora_texto=fecha_str
            )
        except Exception as e:
            print("Error enviando push notification desde web:", e)
        
    return {"status": "ok", "message": "Notificacion enviada y cita confirmada"}

from typing import Any, Dict

class SupabaseWebhookPayload(BaseModel):
    type: str
    table: str
    record: Dict[str, Any]
    schema_name: str | None = None
    old_record: Any = None

@router.post("/supabase-webhook")
def supabase_webhook(payload: SupabaseWebhookPayload):
    from services.push_notifications import enviar_notificacion_nueva_cita
    
    if payload.type != "INSERT" or payload.table != "appointments":
        return {"status": "ignorado", "motivo": "No es un INSERT en appointments"}
        
    record = payload.record
    
    # Si la cita NO está en pending, la creó el bot de IA o se creó manual.
    # Esas vías ya envían notificación, así que solo notificamos las web (pending).
    if record.get("status") != "pending":
        return {"status": "ignorado", "motivo": "Cita no es pending"}

    res_apt = client_supabase.table("appointments").select("*, services(name), businesses(fcm_token)").eq("id", record["id"]).execute()
    if not res_apt.data:
        return {"error": "Cita no encontrada en BD"}
        
    apt = res_apt.data[0]
    fcm_token = apt.get("businesses", {}).get("fcm_token")
    if fcm_token:
        nombre_cliente = apt.get("client_name", "Cliente")
        nombre_servicio = apt.get("services", {}).get("name", "Servicio")
        
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(apt.get("scheduled_at", ""))
            fecha_str = dt.strftime("%Y-%m-%d %H:%M")
        except:
            fecha_str = apt.get("scheduled_at", "una fecha")
            
        try:
            enviar_notificacion_nueva_cita(
                fcm_token=fcm_token,
                nombre_cliente=nombre_cliente,
                servicio=nombre_servicio,
                fecha_hora_texto=fecha_str
            )
        except Exception as e:
            print("Error enviando push notification desde supabase webhook:", e)
        
    return {"status": "ok", "message": "Notificacion enviada desde supabase webhook"}
