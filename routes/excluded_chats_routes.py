from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.db import (
    listar_chats_excluidos,
    agregar_chat_excluido,
    eliminar_chat_excluido,
)
from services.auth import obtener_usuario_actual, verificar_dueno

router = APIRouter()


class ExcludedChatCreate(BaseModel):
    business_id: str
    phone_number: str


@router.get("/excluded-chats")
def listar(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Lista los numeros que el bot tiene excluidos para un negocio."""
    verificar_dueno(business_id, user_id)
    return {"excluded_chats": listar_chats_excluidos(business_id)}


@router.post("/excluded-chats")
def agregar(data: ExcludedChatCreate, user_id: str = Depends(obtener_usuario_actual)):
    """
    Excluye un numero del bot para este negocio: el asistente dejara de
    responderle automaticamente (util para familiares, proveedores u otros
    contactos que escriben al mismo WhatsApp del negocio).
    """
    verificar_dueno(data.business_id, user_id)
    telefono = "".join(c for c in data.phone_number if c.isdigit())
    if len(telefono) < 7:
        raise HTTPException(status_code=400, detail="Numero invalido")
    chat = agregar_chat_excluido(data.business_id, telefono)
    return {"excluded_chat": chat}


@router.delete("/excluded-chats")
def eliminar(
    business_id: str,
    phone_number: str,
    user_id: str = Depends(obtener_usuario_actual),
):
    """Quita un numero de la lista de exclusion (el bot vuelve a responderle)."""
    verificar_dueno(business_id, user_id)
    eliminar_chat_excluido(business_id, phone_number)
    return {"eliminado": True}
