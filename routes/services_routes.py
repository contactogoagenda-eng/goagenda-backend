from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from typing import Optional

from services.db import supabase
from services.auth import obtener_usuario_actual, verificar_acceso_negocio, verificar_dueno

router = APIRouter(tags=["services"])


class ServicePaymentFields(BaseModel):
    """
    Configuracion de abono para agendar este servicio. Si requires_payment
    es true, el bot genera un link de pago de Wompi al agendar (ver
    agent/tools.py:crear_cita) por el monto que resulte de payment_type:
    'percentage' calcula payment_percentage% del precio del servicio,
    'fixed' usa payment_fixed_amount_cents tal cual (en centavos).
    """

    requires_payment: Optional[bool] = None
    payment_type: Optional[str] = None  # 'percentage' | 'fixed'
    payment_percentage: Optional[float] = None
    payment_fixed_amount_cents: Optional[int] = None
    payment_description: Optional[str] = None

    @model_validator(mode="after")
    def _validar_configuracion_pago(self):
        if not self.requires_payment:
            return self

        if self.payment_type not in ("percentage", "fixed"):
            raise ValueError("payment_type debe ser 'percentage' o 'fixed' cuando requires_payment es true")

        if self.payment_type == "percentage":
            if self.payment_percentage is None or not (0 < self.payment_percentage <= 100):
                raise ValueError("payment_percentage debe estar entre 0 (exclusivo) y 100")
        elif self.payment_type == "fixed":
            if self.payment_fixed_amount_cents is None or self.payment_fixed_amount_cents <= 0:
                raise ValueError("payment_fixed_amount_cents debe ser mayor a 0")

        return self


class ServiceCreate(ServicePaymentFields):
    business_id: str
    name: str
    duration_minutes: int = 30
    price: float = 0
    offers_home_visit: bool = False


class ServiceUpdate(ServicePaymentFields):
    name: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[float] = None
    offers_home_visit: Optional[bool] = None


def _business_id_de_servicio(service_id: str) -> str:
    """Busca a que negocio pertenece un servicio (para validar permisos)."""
    response = (
        supabase.table("services")
        .select("business_id")
        .eq("id", service_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    return response.data[0]["business_id"]


@router.get("/services")
def list_services(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    Lista los servicios ACTIVOS de un negocio. Lectura permitida al dueño y a
    cualquier empleado activo (ej. para que un empleado vea el catalogo al
    mostrar sus propias citas).
    """
    verificar_acceso_negocio(business_id, user_id)
    response = (
        supabase.table("services")
        .select("*")
        .eq("business_id", business_id)
        .eq("active", True)
        .execute()
    )
    return {"services": response.data}


@router.post("/services")
def create_service(data: ServiceCreate, user_id: str = Depends(obtener_usuario_actual)):
    """Crea un nuevo servicio para un negocio."""
    verificar_dueno(data.business_id, user_id)
    response = (
        supabase.table("services")
        .insert(
            {
                "business_id": data.business_id,
                "name": data.name,
                "duration_minutes": data.duration_minutes,
                "price": data.price,
                "offers_home_visit": data.offers_home_visit,
                "active": True,
                "requires_payment": data.requires_payment or False,
                "payment_type": data.payment_type,
                "payment_percentage": data.payment_percentage,
                "payment_fixed_amount_cents": data.payment_fixed_amount_cents,
                "payment_description": data.payment_description,
            }
        )
        .execute()
    )
    return {"service": response.data[0] if response.data else None}


@router.put("/services/{service_id}")
def update_service(
    service_id: str,
    data: ServiceUpdate,
    user_id: str = Depends(obtener_usuario_actual),
):
    """Edita un servicio existente (nombre, duracion y/o precio)."""
    verificar_dueno(_business_id_de_servicio(service_id), user_id)

    update_fields = {k: v for k, v in data.dict().items() if v is not None}
    # Al desactivar el abono, se limpia el resto de la configuracion de pago
    # para no dejar un payment_type/monto huerfano que confunda mas adelante.
    if update_fields.get("requires_payment") is False:
        update_fields.update(
            {"payment_type": None, "payment_percentage": None, "payment_fixed_amount_cents": None, "payment_description": None}
        )
    if not update_fields:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")

    response = (
        supabase.table("services")
        .update(update_fields)
        .eq("id", service_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    return {"service": response.data[0]}


@router.delete("/services/{service_id}")
def delete_service(service_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    'Elimina' un servicio mediante borrado logico (lo marca como inactivo).
    Esto evita romper citas existentes que ya usan este servicio,
    y preserva el historial/estadisticas del negocio.
    """
    verificar_dueno(_business_id_de_servicio(service_id), user_id)
    response = (
        supabase.table("services")
        .update({"active": False})
        .eq("id", service_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    return {"deleted": True, "service": response.data[0]}
