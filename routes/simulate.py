from fastapi import APIRouter
from pydantic import BaseModel

from services.ai_agent import procesar_mensaje

router = APIRouter()

# Guardamos el historial en memoria solo para pruebas (se pierde al reiniciar el servidor)
# Cada entrada: {"historial": [...], "ultima_actividad": "iso_timestamp"}
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
    estado_previo = historiales_simulados.get(key, {})
    historial_previo = estado_previo.get("historial", [])
    ultima_actividad_previa = estado_previo.get("ultima_actividad")

    respuesta, nuevo_historial, nueva_ultima_actividad = procesar_mensaje(
        mensaje_cliente=data.mensaje,
        business_id=data.business_id,
        client_phone=data.client_phone,
        historial=historial_previo,
        ultima_actividad=ultima_actividad_previa,
    )

    historiales_simulados[key] = {
        "historial": nuevo_historial,
        "ultima_actividad": nueva_ultima_actividad,
    }

    return {"respuesta_ia": respuesta}


@router.post("/simulate-reset")
def simulate_reset(business_id: str, client_phone: str):
    """Reinicia el historial de conversacion simulada (para empezar una prueba limpia)."""
    key = f"{business_id}:{client_phone}"
    historiales_simulados.pop(key, None)
    return {"status": "historial reiniciado"}