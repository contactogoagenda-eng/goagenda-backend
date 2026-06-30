from fastapi import APIRouter
from pydantic import BaseModel

from services.ai_agent import procesar_mensaje

router = APIRouter()

# Guardamos el historial en memoria solo para pruebas (se pierde al reiniciar el servidor)
historiales_simulados = {}


class SimulateMessageInput(BaseModel):
    business_id: str
    client_phone: str
    mensaje: str


@router.post("/simulate-message")
def simulate_message(data: SimulateMessageInput):
    """
    Endpoint de prueba: simula que un cliente escribio un mensaje por WhatsApp,
    sin necesitar que la API de WhatsApp este funcionando.
    """
    key = f"{data.business_id}:{data.client_phone}"
    historial_previo = historiales_simulados.get(key, [])

    respuesta, nuevo_historial = procesar_mensaje(
        mensaje_cliente=data.mensaje,
        business_id=data.business_id,
        client_phone=data.client_phone,
        historial=historial_previo,
    )

    historiales_simulados[key] = nuevo_historial

    return {"respuesta_ia": respuesta}


@router.post("/simulate-reset")
def simulate_reset(business_id: str, client_phone: str):
    """Reinicia el historial de conversacion simulada (para empezar una prueba limpia)."""
    key = f"{business_id}:{client_phone}"
    historiales_simulados.pop(key, None)
    return {"status": "historial reiniciado"}