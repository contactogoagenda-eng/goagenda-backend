from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.db import supabase
from services.auth import obtener_usuario_actual

router = APIRouter()


class BusinessCreate(BaseModel):
    name: str
    business_type: str | None = None
    phone_number: str | None = None


@router.post("/businesses")
def crear_negocio(data: BusinessCreate, user_id: str = Depends(obtener_usuario_actual)):
    """
    Crea el negocio del usuario autenticado (registro self-service desde la
    app). El owner_id sale SIEMPRE del token, nunca del body, para que nadie
    pueda crear negocios a nombre de otro usuario. Es idempotente: si el
    usuario ya tiene un negocio, devuelve ese en vez de crear un duplicado.
    """
    existente = (
        supabase.table("businesses")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )
    if existente.data:
        return {"business": existente.data[0], "ya_existia": True}

    nombre = data.name.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre del negocio es obligatorio")

    nuevo = {
        "owner_id": user_id,
        "name": nombre,
        "reminder_hours_before": 24,
    }
    if data.business_type and data.business_type.strip():
        nuevo["business_type"] = data.business_type.strip()
    if data.phone_number and data.phone_number.strip():
        nuevo["phone_number"] = data.phone_number.strip()

    response = supabase.table("businesses").insert(nuevo).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="No se pudo crear el negocio")
    business = response.data[0]

    # Horario por defecto (lunes a sabado 9am-7pm, domingo cerrado) para que
    # el bot pueda agendar desde el primer dia; el dueño lo ajusta en la app.
    try:
        filas_horario = [
            {
                "business_id": business["id"],
                "day": dia,
                "is_open": True,
                "opening_time": "09:00",
                "closing_time": "19:00",
            }
            for dia in ["mon", "tue", "wed", "thu", "fri", "sat"]
        ]
        filas_horario.append(
            {"business_id": business["id"], "day": "sun", "is_open": False}
        )
        supabase.table("business_hours").insert(filas_horario).execute()
    except Exception as e:
        # Si falla, el negocio queda creado igual; el dueño configura su
        # horario manualmente en la pantalla de Horarios.
        print(f"No se pudo crear el horario por defecto del negocio {business['id']}: {e}")

    return {"business": business, "ya_existia": False}
