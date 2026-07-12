import os

from fastapi import Header, HTTPException
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
    (columna owner_id). Lanza 404 si no existe y 403 si es de otro usuario.
    """
    respuesta = (
        supabase.table("businesses")
        .select("owner_id")
        .eq("id", business_id)
        .execute()
    )
    if not respuesta.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    if respuesta.data[0].get("owner_id") != user_id:
        raise HTTPException(status_code=403, detail="No tienes permiso sobre este negocio")


def requiere_api_key_interna(x_api_key: str = Header(default=None)):
    """
    Dependencia para endpoints server-to-server (baileys-service) y de
    prueba/administracion: exige el header x-api-key con la key interna.
    """
    if not INTERNAL_API_KEY or x_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="API key invalida o faltante")
