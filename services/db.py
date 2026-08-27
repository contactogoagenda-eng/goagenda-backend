import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

SUPABASE_SERVER_KEY = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY

if not SUPABASE_URL or not SUPABASE_SERVER_KEY:
    raise RuntimeError(
        "Faltan variables de Supabase: define SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY (o SUPABASE_KEY)."
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVER_KEY)


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


def get_confirmed_appointments_for_day(business_id: str, fecha: str, employee_id: str | None = None):
    """
    Trae las citas confirmadas de un negocio para un dia especifico (fecha 'YYYY-MM-DD'),
    junto con la duracion de su servicio. Se usa para detectar choques de horario.
    Si se pasa employee_id, solo trae las citas de ese empleado (cada uno
    tiene su propia agenda, asi que dos empleados pueden tener una cita a
    la misma hora sin que sea un choque real).
    """
    inicio_dia = f"{fecha}T00:00:00"
    fin_dia = f"{fecha}T23:59:59"

    query = (
        supabase.table("appointments")
        .select("*, services(duration_minutes)")
        .eq("business_id", business_id)
        .eq("status", "confirmed")
        .gte("scheduled_at", inicio_dia)
        .lte("scheduled_at", fin_dia)
    )
    if employee_id:
        query = query.eq("employee_id", employee_id)
    response = query.execute()
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


def create_appointment(
    business_id: str, client_phone: str, client_name: str, service_id: str, scheduled_at: str, employee_id: str
):
    """Crea una nueva cita, ligada al empleado que la atiende."""
    response = (
        supabase.table("appointments")
        .insert(
            {
                "business_id": business_id,
                "client_phone": client_phone,
                "client_name": client_name,
                "service_id": service_id,
                "scheduled_at": scheduled_at,
                "employee_id": employee_id,
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
        .select("phone_number")
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


def get_appointment_by_id_and_phone(appointment_id: str, client_phone: str):
    """
    Trae una cita (con el nombre y duracion del servicio) validando que
    pertenezca al telefono dado, para cancelaciones y reprogramaciones.
    Retorna None si no existe o no es de ese cliente.
    """
    response = (
        supabase.table("appointments")
        .select("*, services(name, duration_minutes)")
        .eq("id", appointment_id)
        .eq("client_phone", client_phone)
        .execute()
    )
    if response.data:
        return response.data[0]
    return None


def update_appointment_schedule(appointment_id: str, nueva_fecha_hora: str):
    """Actualiza la fecha/hora de una cita existente (reprogramacion)."""
    response = (
        supabase.table("appointments")
        .update({"scheduled_at": nueva_fecha_hora})
        .eq("id", appointment_id)
        .execute()
    )
    return response.data


DIAS_VALIDOS_HORARIO = ["mon", "tue", "wed", "thu", "fri", "sat"]


def get_employees(business_id: str, solo_activos: bool = True):
    """Lista los empleados de un negocio (el dueño incluido, role='owner')."""
    query = supabase.table("employees").select("*").eq("business_id", business_id)
    if solo_activos:
        query = query.eq("active", True)
    response = query.order("created_at").execute()
    return response.data


def get_employee_by_id(employee_id: str):
    response = supabase.table("employees").select("*").eq("id", employee_id).execute()
    if response.data:
        return response.data[0]
    return None


def create_employee(business_id: str, user_id: str, name: str | None, role: str) -> dict:
    """
    Crea un empleado (role='owner' para el dueño al reclamar su negocio, o
    'staff' para un empleado normal) y le siembra un horario por defecto
    (lunes a sabado 9am-7pm, domingo cerrado) para que ya pueda recibir
    citas; el dueño lo ajusta despues desde la app.
    """
    response = (
        supabase.table("employees")
        .insert({"business_id": business_id, "user_id": user_id, "name": name, "role": role})
        .execute()
    )
    empleado = response.data[0]

    try:
        filas_horario = [
            {
                "employee_id": empleado["id"],
                "day": dia,
                "is_open": True,
                "opening_time": "09:00",
                "closing_time": "19:00",
            }
            for dia in DIAS_VALIDOS_HORARIO
        ]
        filas_horario.append({"employee_id": empleado["id"], "day": "sun", "is_open": False})
        supabase.table("employee_hours").insert(filas_horario).execute()
    except Exception as e:
        print(f"No se pudo crear el horario por defecto del empleado {empleado['id']}: {e}")

    return empleado


def update_employee(employee_id: str, campos: dict) -> dict | None:
    response = supabase.table("employees").update(campos).eq("id", employee_id).execute()
    return response.data[0] if response.data else None


def get_employee_hours(employee_id: str):
    response = supabase.table("employee_hours").select("*").eq("employee_id", employee_id).execute()
    return response.data


def get_employee_hours_for_day(employee_id: str, dia_codigo: str):
    response = (
        supabase.table("employee_hours")
        .select("*")
        .eq("employee_id", employee_id)
        .eq("day", dia_codigo)
        .execute()
    )
    if response.data:
        return response.data[0]
    return None


def upsert_employee_hours(employee_id: str, dia: str, campos: dict) -> dict | None:
    fila = {"employee_id": employee_id, "day": dia, **campos}
    response = supabase.table("employee_hours").upsert(fila, on_conflict="employee_id,day").execute()
    return response.data[0] if response.data else None


def get_employee_services(employee_id: str):
    """Servicios activos a los que esta ligado un empleado."""
    response = (
        supabase.table("employee_services")
        .select("service_id, services(*)")
        .eq("employee_id", employee_id)
        .execute()
    )
    return [fila["services"] for fila in response.data if fila.get("services") and fila["services"].get("active")]


def set_employee_services(employee_id: str, service_ids: list[str]):
    """Reemplaza por completo la lista de servicios ligados a un empleado."""
    supabase.table("employee_services").delete().eq("employee_id", employee_id).execute()
    if service_ids:
        filas = [{"employee_id": employee_id, "service_id": sid} for sid in service_ids]
        supabase.table("employee_services").insert(filas).execute()