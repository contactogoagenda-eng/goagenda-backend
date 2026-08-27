import os

from fastapi import Depends, Header, HTTPException
from dotenv import load_dotenv

from services.db import supabase

load_dotenv()

# La misma key interna que usa el baileys-service (header x-api-key).
# Protege los endpoints server-to-server y los de prueba/administracion.
INTERNAL_API_KEY = os.getenv("BAILEYS_INTERNAL_API_KEY", "")


def obtener_usuario_actual(authorization: str = Header(default=None)) -> str:
    """
    Dependencia de FastAPI: valida el JWT de Supabase Auth que manda la app
    (header "Authorization: Bearer <token>") y devuelve el user_id.
    Lanza 401 si falta el token o no es valido.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Falta el token de autorizacion")

    token = authorization.split(" ", 1)[1].strip()
    try:
        respuesta = supabase.auth.get_user(token)
        user = respuesta.user
    except Exception:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")

    if not user:
        raise HTTPException(status_code=401, detail="Token invalido o expirado")
    return user.id


def verificar_dueno(business_id: str, user_id: str):
    """
    Verifica que el negocio exista y pertenezca al usuario autenticado
    (columna owner_id). Lanza 404 si no existe, 403 si es de otro usuario,
    y 403 tambien si el negocio esta bloqueado por un super admin: el
    bloqueo congela toda la gestion del negocio desde la app, no solo el
    chat publico de sus clientes.
    """
    respuesta = (
        supabase.table("businesses")
        .select("owner_id, blocked")
        .eq("id", business_id)
        .execute()
    )
    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    if respuesta.data[0].get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="No tienes permiso sobre este negocio")
    if respuesta.data[0].get("blocked"):
        raise HTTPException(status_code=403, detail="Este negocio esta bloqueado")


def verificar_acceso_negocio(business_id: str, user_id: str):
    """
    Como verificar_dueno, pero de solo lectura: permite tanto al dueño como
    a cualquier empleado activo de este negocio (ej. para que un empleado
    vea el WhatsApp del negocio en su propio enlace de chat). Lanza 404 si
    el negocio no existe, 403 si el usuario no es dueño ni empleado activo,
    o si el negocio esta bloqueado.
    """
    respuesta = (
        supabase.table("businesses")
        .select("owner_id, blocked")
        .eq("id", business_id)
        .execute()
    )
    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    if respuesta.data[0].get("blocked"):
        raise HTTPException(status_code=403, detail="Este negocio esta bloqueado")
    if respuesta.data[0].get("owner_id") == user_id:
        return

    empleado = (
        supabase.table("employees")
        .select("id")
        .eq("business_id", business_id)
        .eq("user_id", user_id)
        .eq("active", True)
        .execute()
    )
    if not empleado.data:
        raise HTTPException(status_code=403, detail="No tienes permiso sobre este negocio")


def es_super_admin(user_id: str) -> bool:
    respuesta = (
        supabase.table("super_admins")
        .select("user_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return bool(respuesta.data)


def requiere_super_admin(user_id: str = Depends(obtener_usuario_actual)) -> str:
    """
    Dependencia para los endpoints exclusivos del super admin (crear
    negocios, generar codigos de invitacion tipo 'admin', bloquear
    negocios). Requiere ademas el JWT valido de obtener_usuario_actual.
    """
    if not es_super_admin(user_id):
        raise HTTPException(status_code=403, detail="Requiere rol de super admin")
    return user_id


def verificar_acceso_empleado(employee_id: str, user_id: str) -> str:
    """
    Permite gestionar los datos de un empleado (horario, servicios,
    nombre) al dueño de su negocio, o al propio empleado sobre su propia
    fila. Lanza 404 si el empleado no existe, y 403 si quien pide no es ni
    el dueño ni ese mismo empleado, o si el negocio esta bloqueado.
    Devuelve el business_id del empleado, para que el caller no tenga que
    volver a buscarlo.
    """
    respuesta = (
        supabase.table("employees")
        .select("business_id, user_id")
        .eq("id", employee_id)
        .execute()
    )
    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")

    empleado = respuesta.data[0]
    business_id = empleado["business_id"]
    if empleado["user_id"] == user_id:
        return business_id

    negocio = (
        supabase.table("businesses")
        .select("owner_id, blocked")
        .eq("id", business_id)
        .execute()
    )
    if not negocio.data or negocio.data[0].get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="No tienes permiso sobre este empleado")
    if negocio.data[0].get("blocked"):
        raise HTTPException(status_code=403, detail="Este negocio esta bloqueado")
    return business_id


def requiere_api_key_interna(x_api_key: str = Header(default=None)):
    """
    Dependencia para endpoints server-to-server (baileys-service) y de
    prueba/administracion: exige el header x-api-key con la key interna.
    """
    if not INTERNAL_API_KEY or x_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="API key invalida o faltante")
