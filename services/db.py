import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_business_by_phone(phone_number: str):
    """Busca un negocio por su número de WhatsApp (el número del negocio, no del cliente)."""
    response = supabase.table("businesses").select("*").eq("phone_number", phone_number).execute()
    if response.data:
        return response.data[0]
    return None


def get_business_by_id(business_id: str):
    """Obtiene la informacion completa de un negocio (nombre, horario, etc)."""
    response = supabase.table("businesses").select("*").eq("id", business_id).execute()
    if response.data:
        return response.data[0]
    return None


def get_conversation_state(business_id: str, client_phone: str):
    """Obtiene el estado de conversación actual de un cliente con un negocio."""
    response = (
        supabase.table("conversation_state")
        .select("*")
        .eq("business_id", business_id)
        .eq("client_phone", client_phone)
        .execute()
    )
    if response.data:
        return response.data[0]
    return None


def upsert_conversation_state(business_id: str, client_phone: str, step: str, context: dict):
    """Crea o actualiza el estado de conversación de un cliente."""
    response = (
        supabase.table("conversation_state")
        .upsert(
            {
                "business_id": business_id,
                "client_phone": client_phone,
                "step": step,
                "context": context,
            },
            on_conflict="business_id,client_phone",
        )
        .execute()
    )
    return response.data


def get_confirmed_appointments_for_day(business_id: str, fecha: str):
    """
    Trae todas las citas confirmadas de un negocio para un dia especifico (fecha 'YYYY-MM-DD'),
    junto con la duracion de su servicio. Se usa para detectar choques de horario.
    """
    inicio_dia = f"{fecha}T00:00:00"
    fin_dia = f"{fecha}T23:59:59"

    response = (
        supabase.table("appointments")
        .select("*, services(duration_minutes)")
        .eq("business_id", business_id)
        .eq("status", "confirmed")
        .gte("scheduled_at", inicio_dia)
        .lte("scheduled_at", fin_dia)
        .execute()
    )
    return response.data


def get_services(business_id: str):
    """Lista los servicios ACTIVOS disponibles de un negocio."""
    response = (
        supabase.table("services")
        .select("*")
        .eq("business_id", business_id)
        .eq("active", True)
        .execute()
    )
    return response.data


def create_appointment(business_id: str, client_phone: str, client_name: str, service_id: str, scheduled_at: str):
    """Crea una nueva cita."""
    response = (
        supabase.table("appointments")
        .insert(
            {
                "business_id": business_id,
                "client_phone": client_phone,
                "client_name": client_name,
                "service_id": service_id,
                "scheduled_at": scheduled_at,
                "status": "confirmed",
            }
        )
        .execute()
    )
    return response.data

def get_business_by_whatsapp_phone_id(phone_number_id: str):
    """
    Busca el negocio que tiene este phone_number_id de WhatsApp asociado.
    Se usa en el webhook para identificar a que negocio pertenece cada
    mensaje entrante, permitiendo multiples negocios con numeros distintos.
    Retorna None si no se encuentra ningun negocio con ese numero.
    """
    response = (
        supabase.table("businesses")
        .select("*")
        .eq("whatsapp_phone_number_id", phone_number_id)
        .limit(1)
        .execute()
    )
    if response.data:
        return response.data[0]
    return None

def cancel_appointment(appointment_id: str, client_phone: str):
    """
    Cancela una cita existente, validando que pertenezca al cliente que la solicita
    (filtra por client_phone para evitar que se cancele la cita de otro cliente).
    """
    response = (
        supabase.table("appointments")
        .update({"status": "cancelled"})
        .eq("id", appointment_id)
        .eq("client_phone", client_phone)
        .execute()
    )

    if not response.data:
        return {"error": "No se encontro esa cita para este cliente, no se cancelo nada."}

    return response.data

def esta_chat_excluido(business_id: str, client_phone: str) -> bool:
    """
    Revisa si un numero de cliente esta en la lista de exclusion de un
    negocio (ej. familiares, proveedores u otros contactos que le escriben
    al mismo WhatsApp del negocio y no deben recibir respuestas del bot).
    """
    response = (
        supabase.table("excluded_chats")
        .select("id")
        .eq("business_id", business_id)
        .eq("phone_number", client_phone)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def listar_chats_excluidos(business_id: str):
    """Lista los numeros excluidos del bot para un negocio."""
    response = (
        supabase.table("excluded_chats")
        .select("*")
        .eq("business_id", business_id)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data


def agregar_chat_excluido(business_id: str, phone_number: str):
    """Agrega (o reafirma) un numero a la lista de exclusion de un negocio."""
    response = (
        supabase.table("excluded_chats")
        .upsert(
            {"business_id": business_id, "phone_number": phone_number},
            on_conflict="business_id,phone_number",
        )
        .execute()
    )
    return response.data[0] if response.data else None


def eliminar_chat_excluido(business_id: str, phone_number: str):
    """Quita un numero de la lista de exclusion (el bot vuelve a responderle)."""
    supabase.table("excluded_chats").delete().eq("business_id", business_id).eq(
        "phone_number", phone_number
    ).execute()


def get_client_appointments(business_id: str, client_phone: str):
    """Obtiene las citas activas de un cliente en un negocio."""
    response = (
        supabase.table("appointments")
        .select("*")
        .eq("business_id", business_id)
        .eq("client_phone", client_phone)
        .eq("status", "confirmed")
        .execute()
    )
    return response.data