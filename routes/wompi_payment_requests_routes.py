from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from services.auth import obtener_usuario_actual, verificar_acceso_negocio
from services.wompi_payment_requests import listar_solicitudes_pago, verificar_estado_solicitud

router = APIRouter(prefix="/wompi/payment-requests", tags=["wompi"])


@router.get("")
def listar_solicitudes(
    business_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    user_id: str = Depends(obtener_usuario_actual),
):
    """
    Historial de solicitudes de abono generadas desde el chat (Ajustes >
    Pagos). Lectura permitida al dueño y a cualquier empleado activo, igual
    que el catalogo de servicios.
    """
    verificar_acceso_negocio(business_id, user_id)
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit debe estar entre 1 y 200")
    return {"payment_requests": listar_solicitudes_pago(business_id, status=status, limit=limit)}


@router.post("/{request_id}/check")
def verificar_solicitud(request_id: str, business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    Reconciliacion manual: reintenta confirmar el estado de una solicitud
    de pago contra Wompi, por si el webhook automatico no llego. Ver la
    limitacion documentada en services/wompi_payment_requests.py:
    verificar_estado_solicitud (solo se puede reconsultar si ya llego al
    menos un evento previo).
    """
    verificar_acceso_negocio(business_id, user_id)
    try:
        resultado = verificar_estado_solicitud(business_id, request_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return resultado
