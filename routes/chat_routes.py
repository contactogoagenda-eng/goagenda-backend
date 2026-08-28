import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.graph import MENSAJE_NEGOCIO_NO_DISPONIBLE, enviar_mensaje, obtener_historial
from services.db import get_business_by_id, get_employee_by_id

router = APIRouter(prefix="/chat", tags=["chat"])


def _obtener_negocio_o_404(business_id: str) -> dict:
    business = get_business_by_id(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return business


def _obtener_empleado_o_404(business_id: str, employee_id: str) -> dict:
    """
    Valida el empleado del enlace de chat individual: debe existir,
    pertenecer a este negocio, y estar activo. 404 en cualquier otro caso
    (no distinguimos el motivo para no filtrar informacion interna).
    """
    empleado = get_employee_by_id(employee_id)
    if not empleado or empleado["business_id"] != business_id or not empleado.get("active"):
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    return empleado


def _negocio_habilitado(business: dict) -> bool:
    return business.get("plan", "basic") != "none" and not business.get("blocked")


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
    employee_id: str | None = None
    employee_name: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatMessageInput(BaseModel):
    mensaje: str


class ChatOption(BaseModel):
    """Opcion de seleccion rapida (boton) para el widget de chat: el cliente toca en vez de escribir."""

    label: str
    value: str


class ChatMessageResponse(BaseModel):
    respuesta: str
    opciones: list[ChatOption] | None = None


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
    de "none" y no bloqueado). El enlace `/chat/<business_id>` es el
    enlace general del negocio: el cliente elige el empleado conversando.
    """
    business = _obtener_negocio_o_404(business_id)
    return ChatConfigResponse(
        business_id=business["id"],
        name=business.get("name", ""),
        business_type=business.get("business_type"),
        enabled=_negocio_habilitado(business),
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

    if not _negocio_habilitado(business):
        return ChatMessageResponse(respuesta=MENSAJE_NEGOCIO_NO_DISPONIBLE)

    respuesta, opciones = enviar_mensaje(business_id, session_id, data.mensaje)
    return ChatMessageResponse(respuesta=respuesta, opciones=opciones)


@router.get("/{business_id}/sessions/{session_id}/messages", response_model=ChatHistoryResponse)
def obtener_historial_chat(business_id: str, session_id: str):
    """Historial de una sesion (para recargar el chat si el cliente refresca la pagina)."""
    _obtener_negocio_o_404(business_id)
    session_id = _validar_session_id(session_id)
    historial = obtener_historial(business_id, session_id)
    return ChatHistoryResponse(session_id=session_id, mensajes=historial)


# ---------------------------------------------------------------------
# Chat propio de un empleado: mismo contrato que arriba, pero con el
# empleado fijo (el cliente no lo elige conversando, ya viene en la URL).
# ---------------------------------------------------------------------


@router.get("/{business_id}/{employee_id}/config", response_model=ChatConfigResponse)
def obtener_config_chat_empleado(business_id: str, employee_id: str):
    business = _obtener_negocio_o_404(business_id)
    empleado = _obtener_empleado_o_404(business_id, employee_id)
    return ChatConfigResponse(
        business_id=business["id"],
        name=business.get("name", ""),
        business_type=business.get("business_type"),
        enabled=_negocio_habilitado(business),
        employee_id=empleado["id"],
        employee_name=empleado.get("name"),
    )


@router.post("/{business_id}/{employee_id}/sessions", response_model=CreateSessionResponse)
def crear_sesion_chat_empleado(business_id: str, employee_id: str):
    _obtener_negocio_o_404(business_id)
    _obtener_empleado_o_404(business_id, employee_id)
    return CreateSessionResponse(session_id=str(uuid.uuid4()))


@router.post("/{business_id}/{employee_id}/sessions/{session_id}/messages", response_model=ChatMessageResponse)
def enviar_mensaje_chat_empleado(business_id: str, employee_id: str, session_id: str, data: ChatMessageInput):
    business = _obtener_negocio_o_404(business_id)
    _obtener_empleado_o_404(business_id, employee_id)
    session_id = _validar_session_id(session_id)

    if not _negocio_habilitado(business):
        return ChatMessageResponse(respuesta=MENSAJE_NEGOCIO_NO_DISPONIBLE)

    respuesta, opciones = enviar_mensaje(business_id, session_id, data.mensaje, employee_id=employee_id)
    return ChatMessageResponse(respuesta=respuesta, opciones=opciones)


@router.get("/{business_id}/{employee_id}/sessions/{session_id}/messages", response_model=ChatHistoryResponse)
def obtener_historial_chat_empleado(business_id: str, employee_id: str, session_id: str):
    _obtener_negocio_o_404(business_id)
    _obtener_empleado_o_404(business_id, employee_id)
    session_id = _validar_session_id(session_id)
    historial = obtener_historial(business_id, session_id)
    return ChatHistoryResponse(session_id=session_id, mensajes=historial)
