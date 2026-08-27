from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.db import supabase
from services.auth import obtener_usuario_actual, verificar_acceso_negocio, verificar_dueno

router = APIRouter(tags=["services"])


class ServiceCreate(BaseModel):
    business_id: str
    name: str
    duration_minutes: int = 30
    price: float = 0


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    duration_minutes: Optional[int] = None
    price: Optional[float] = None


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
                "active": True,
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
