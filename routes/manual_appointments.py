from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime

from services.db import get_services, create_appointment
from services.ai_agent import _es_hora_valida, _hay_choque_de_horario

router = APIRouter()


class CrearCitaManualInput(BaseModel):
    business_id: str
    client_name: str
    client_phone: str
    service_id: str
    fecha_hora: str  # formato ISO 8601, ej: 2026-07-01T15:00:00


@router.post("/appointments/manual")
def crear_cita_manual(data: CrearCitaManualInput):
    """
    Crea una cita directamente desde el panel (no desde WhatsApp), para
    cuando un cliente llega en persona o llama por telefono. Reutiliza
    las mismas validaciones de horario, almuerzo y choques que usa la IA,
    para que la agenda nunca quede inconsistente sin importar el canal
    por el que entro la cita.
    """
    es_valida, mensaje_error = _es_hora_valida(data.fecha_hora, data.business_id)
    if not es_valida:
        raise HTTPException(status_code=400, detail=mensaje_error)

    servicios = get_services(data.business_id)
    servicio = next((s for s in servicios if s["id"] == data.service_id), None)
    if not servicio:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")

    fecha_hora_dt = datetime.fromisoformat(data.fecha_hora)
    duracion = servicio.get("duration_minutes", 30)

    hay_choque, mensaje_choque = _hay_choque_de_horario(data.business_id, fecha_hora_dt, duracion)
    if hay_choque:
        raise HTTPException(status_code=409, detail=mensaje_choque)

    resultado = create_appointment(
        business_id=data.business_id,
        client_phone=data.client_phone,
        client_name=data.client_name,
        service_id=data.service_id,
        scheduled_at=data.fecha_hora,
    )

    return {"cita_creada": resultado}