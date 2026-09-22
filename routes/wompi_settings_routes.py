from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.auth import obtener_usuario_actual, verificar_dueno
from services.wompi_credentials import (
    eliminar_credenciales,
    guardar_credenciales,
    obtener_estado_credenciales,
    validar_credenciales,
)

router = APIRouter(prefix="/business-settings/wompi", tags=["wompi"])


class WompiCredentialsInput(BaseModel):
    business_id: str
    public_key: str = Field(min_length=10, max_length=255)
    private_key: str = Field(min_length=10, max_length=255)
    events_key: str = Field(min_length=10, max_length=255)
    sandbox_mode: bool = True


@router.get("")
def obtener_configuracion_wompi(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    Estado de la configuracion de Wompi de un negocio (sin exponer las
    llaves, solo si esta configurado, en que ambiente y el resultado de la
    ultima validacion). Solo el dueño puede ver esto.
    """
    verificar_dueno(business_id, user_id)
    estado = obtener_estado_credenciales(business_id)
    if not estado:
        return {"configured": False}
    return {"configured": True, **estado}


@router.put("")
def guardar_configuracion_wompi(data: WompiCredentialsInput, user_id: str = Depends(obtener_usuario_actual)):
    """
    Guarda (crea o reemplaza) las credenciales de Wompi de un negocio,
    encriptadas antes de tocar la base de datos. No valida contra Wompi en
    el mismo paso - usa POST .../validate despues de guardar para
    confirmar que son correctas.
    """
    verificar_dueno(data.business_id, user_id)
    resultado = guardar_credenciales(
        business_id=data.business_id,
        public_key=data.public_key,
        private_key=data.private_key,
        events_key=data.events_key,
        sandbox_mode=data.sandbox_mode,
        user_id=user_id,
    )
    return {"credentials": resultado}


class WompiValidateInput(BaseModel):
    business_id: str


@router.post("/validate")
def validar_configuracion_wompi(
    data: WompiValidateInput, request: Request, user_id: str = Depends(obtener_usuario_actual)
):
    """Prueba las credenciales guardadas contra la API real de Wompi (GET /merchants/info)."""
    verificar_dueno(data.business_id, user_id)
    exito, mensaje = validar_credenciales(data.business_id, user_id)
    if not exito:
        raise HTTPException(status_code=400, detail=mensaje)
    return {"valid": True, "message": mensaje}


@router.delete("")
def eliminar_configuracion_wompi(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Elimina la configuracion de Wompi del negocio (deja de poder generar links de pago hasta reconfigurarla)."""
    verificar_dueno(business_id, user_id)
    eliminar_credenciales(business_id, user_id)
    return {"deleted": True}
