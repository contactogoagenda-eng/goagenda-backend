from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.db import supabase
from services.auth import obtener_usuario_actual, verificar_dueno

router = APIRouter()

DIAS_VALIDOS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class DayHourUpdate(BaseModel):
    business_id: str
    day: str
    is_open: bool
    opening_time: Optional[str] = None  # formato 'HH:MM'
    closing_time: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None


@router.get("/business-hours")
def get_business_hours(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Trae el horario configurado para los 7 dias de un negocio."""
    verificar_dueno(business_id, user_id)
    response = (
        supabase.table("business_hours")
        .select("*")
        .eq("business_id", business_id)
        .execute()
    )
    # Ordenamos en el orden correcto de la semana (lunes a domingo)
    dias_dict = {row["day"]: row for row in response.data}
    horario_ordenado = [dias_dict[d] for d in DIAS_VALIDOS if d in dias_dict]
    return {"business_hours": horario_ordenado}


@router.put("/business-hours")
def update_business_hours(data: DayHourUpdate, user_id: str = Depends(obtener_usuario_actual)):
    """Crea o actualiza el horario de un dia especifico para un negocio."""
    verificar_dueno(data.business_id, user_id)
    if data.day not in DIAS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Dia invalido. Usa uno de: {DIAS_VALIDOS}")

    update_data = {
        "business_id": data.business_id,
        "day": data.day,
        "is_open": data.is_open,
    }
    if data.opening_time:
        update_data["opening_time"] = data.opening_time
    if data.closing_time:
        update_data["closing_time"] = data.closing_time
    update_data["lunch_start"] = data.lunch_start
    update_data["lunch_end"] = data.lunch_end

    response = (
        supabase.table("business_hours")
        .upsert(update_data, on_conflict="business_id,day")
        .execute()
    )
    return {"business_hour": response.data[0] if response.data else None}