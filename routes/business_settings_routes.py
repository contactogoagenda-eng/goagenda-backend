from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.db import supabase

router = APIRouter()


class BusinessInfoUpdate(BaseModel):
    business_id: str
    name: str
    phone_number: str | None = None


@router.put("/business-settings/info")
def update_business_info(data: BusinessInfoUpdate):
    """Actualiza el nombre y telefono del negocio."""
    update_fields = {"name": data.name}
    if data.phone_number is not None:
        update_fields["phone_number"] = data.phone_number

    response = (
        supabase.table("businesses")
        .update(update_fields)
        .eq("id", data.business_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return {"business": response.data[0]}


class FcmTokenUpdate(BaseModel):
    business_id: str
    fcm_token: str


@router.put("/business-settings/fcm-token")
def update_fcm_token(data: FcmTokenUpdate):
    """Guarda o actualiza el token de notificaciones push del dispositivo del negocio."""
    response = (
        supabase.table("businesses")
        .update({"fcm_token": data.fcm_token})
        .eq("id", data.business_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return {"business": response.data[0]}


class ReminderConfigUpdate(BaseModel):
    business_id: str
    reminder_hours_before: int


@router.get("/business-settings")
def get_business_settings(business_id: str):
    """Trae la configuracion general del negocio (incluye horas de recordatorio)."""
    response = supabase.table("businesses").select("*").eq("id", business_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return {"business": response.data[0]}


@router.put("/business-settings/reminder")
def update_reminder_config(data: ReminderConfigUpdate):
    """Actualiza cuantas horas antes de la cita se debe enviar el recordatorio."""
    if data.reminder_hours_before < 0 or data.reminder_hours_before > 168:
        raise HTTPException(status_code=400, detail="Las horas deben estar entre 0 y 168 (1 semana)")

    response = (
        supabase.table("businesses")
        .update({"reminder_hours_before": data.reminder_hours_before})
        .eq("id", data.business_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return {"business": response.data[0]}