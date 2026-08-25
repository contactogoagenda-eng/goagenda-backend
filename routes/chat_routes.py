import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.graph import MENSAJE_NEGOCIO_NO_DISPONIBLE, enviar_mensaje, obtener_historial
from services.db import get_business_by_id

router = APIRouter(prefix="/chat", tags=["chat"])


def _obtener_negocio_o_404(business_id: str) -> dict:
    business = get_business_by_id(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return business


def _validar_session_id(session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="session_id invalido")


class ChatConfigResponse(BaseModel):
    business_id: str
    name: str
    business_type: str | None = None
    enabled: bool


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatMessageInput(BaseModel):
    mensaje: str


class ChatMessageResponse(BaseModel):
    respuesta: str


class ChatHistoryMessage(BaseModel):
    role: str
    content: str


class ChatHistoryResponse(BaseModel):
    session_id: str
    mensajes: list[ChatHistoryMessage]


@router.get("/{business_id}/config", response_model=ChatConfigResponse)
def obtener_config_chat(business_id: str):
    """
    Info publica que el widget de chat necesita antes de arrancar: nombre
    del negocio y si el agendamiento automatico esta activo (plan distinto
    de "none"). El enlace `/chat/<business_id>` es el enlace unico que el
    dueño del negocio comparte con sus clientes para que agenden.
    """
    business = _obtener_negocio_o_404(business_id)
    return ChatConfigResponse(
        business_id=business["id"],
        name=business.get("name", ""),
        business_type=business.get("business_type"),
        enabled=business.get("plan", "basic") != "none",
    )


@router.post("/{business_id}/sessions", response_model=CreateSessionResponse)
def crear_sesion_chat(business_id: str):
    """
    Crea una sesion de chat nueva (conversacion vacia) para este negocio.
    El frontend guarda el session_id (ej: localStorage) y lo reutiliza en
    cada mensaje de esa visita; para empezar de cero basta con crear otra sesion.
    """
    _obtener_negocio_o_404(business_id)
    return CreateSessionResponse(session_id=str(uuid.uuid4()))


@router.post("/{business_id}/sessions/{session_id}/messages", response_model=ChatMessageResponse)
def enviar_mensaje_chat(business_id: str, session_id: str, data: ChatMessageInput):
    """
    Manda un mensaje del cliente al agente de este negocio y devuelve su
    respuesta. Aislado por negocio en dos capas: el business_id de la URL,
    y el thread_id interno del checkpointer (`business_id:session_id`), asi
    que un session_id nunca puede continuar la conversacion de otro negocio.
    """
    business = _obtener_negocio_o_404(business_id)
    session_id = _validar_session_id(session_id)

    if business.get("plan", "basic") == "none":
        return ChatMessageResponse(respuesta=MENSAJE_NEGOCIO_NO_DISPONIBLE)

    respuesta = enviar_mensaje(business_id, session_id, data.mensaje)
    return ChatMessageResponse(respuesta=respuesta)


@router.get("/{business_id}/sessions/{session_id}/messages", response_model=ChatHistoryResponse)
def obtener_historial_chat(business_id: str, session_id: str):
    """Historial de una sesion (para recargar el chat si el cliente refresca la pagina)."""
    _obtener_negocio_o_404(business_id)
    session_id = _validar_session_id(session_id)
    historial = obtener_historial(business_id, session_id)
    return ChatHistoryResponse(session_id=session_id, mensajes=historial)
