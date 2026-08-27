import secrets
import string
from datetime import datetime, timezone

from postgrest.exceptions import APIError

from services.db import supabase

_ALFABETO = string.ascii_uppercase + string.digits
_LARGO_CODIGO = 6
_INTENTOS_MAXIMOS = 5


def _generar_codigo() -> str:
    return "".join(secrets.choice(_ALFABETO) for _ in range(_LARGO_CODIGO))


def crear_codigo_invitacion(business_id: str, role: str, created_by: str, employee_name: str | None = None) -> dict:
    """
    Genera un codigo de invitacion de 6 caracteres (letras + digitos) unico,
    ligado a un negocio y a un rol ('admin' para que un dueño reclame el
    negocio, 'employee' para que un empleado se una a el). Reintenta unas
    pocas veces si por azar choca con un codigo ya existente.
    """
    for _ in range(_INTENTOS_MAXIMOS):
        codigo = _generar_codigo()
        try:
            response = (
                supabase.table("invitation_codes")
                .insert(
                    {
                        "code": codigo,
                        "business_id": business_id,
                        "role": role,
                        "employee_name": employee_name,
                        "created_by": created_by,
                    }
                )
                .execute()
            )
            return response.data[0]
        except APIError as e:
            if getattr(e, "code", None) == "23505":  # unique_violation: codigo repetido, reintenta
                continue
            raise
    raise RuntimeError("No se pudo generar un codigo de invitacion unico, intenta de nuevo.")


def obtener_codigo_sin_usar(code: str) -> dict | None:
    response = (
        supabase.table("invitation_codes")
        .select("*")
        .eq("code", code.strip().upper())
        .is_("used_at", "null")
        .execute()
    )
    if response.data:
        return response.data[0]
    return None


def marcar_codigo_usado(codigo_id: str, user_id: str):
    supabase.table("invitation_codes").update(
        {"used_by": user_id, "used_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", codigo_id).execute()
