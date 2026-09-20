from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from services.auth import obtener_usuario_actual, verificar_acceso_negocio, verificar_dueno
from services.db import supabase

router = APIRouter(tags=["home-visit-zones"])


class ZoneCreate(BaseModel):
    business_id: str
    name: str = Field(min_length=1, max_length=80)
    fee: float = Field(default=0, ge=0)


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    fee: float | None = Field(default=None, ge=0)
    active: bool | None = None


def _business_id_de_zona(zone_id: str) -> str:
    response = supabase.table("home_visit_zones").select("business_id").eq("id", zone_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Zona no encontrada")
    return response.data[0]["business_id"]


@router.get("/home-visit-zones")
def list_zones(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Zonas de domicilio del negocio. Lectura para el dueño y empleados activos."""
    verificar_acceso_negocio(business_id, user_id)
    response = supabase.table("home_visit_zones").select("*").eq("business_id", business_id).order("name").execute()
    return {"zones": response.data}


@router.post("/home-visit-zones")
def create_zone(data: ZoneCreate, user_id: str = Depends(obtener_usuario_actual)):
    verificar_dueno(data.business_id, user_id)
    nombre = data.name.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre de la zona es obligatorio")

    existentes = supabase.table("home_visit_zones").select("name").eq("business_id", data.business_id).execute().data
    if any(z["name"].strip().lower() == nombre.lower() for z in existentes):
        raise HTTPException(status_code=409, detail="Ya existe una zona con ese nombre")

    response = (
        supabase.table("home_visit_zones")
        .insert({"business_id": data.business_id, "name": nombre, "fee": data.fee, "active": True})
        .execute()
    )
    return {"zone": response.data[0] if response.data else None}


@router.put("/home-visit-zones/{zone_id}")
def update_zone(zone_id: str, data: ZoneUpdate, user_id: str = Depends(obtener_usuario_actual)):
    verificar_dueno(_business_id_de_zona(zone_id), user_id)
    campos = {k: v for k, v in data.model_dump().items() if v is not None}
    if "name" in campos:
        campos["name"] = campos["name"].strip()
        if not campos["name"]:
            raise HTTPException(status_code=400, detail="El nombre de la zona es obligatorio")
    if not campos:
        raise HTTPException(status_code=400, detail="No se enviaron campos para actualizar")

    response = supabase.table("home_visit_zones").update(campos).eq("id", zone_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Zona no encontrada")
    return {"zone": response.data[0]}


@router.delete("/home-visit-zones/{zone_id}")
def delete_zone(zone_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Elimina la zona. Las citas ya agendadas conservan el nombre y recargo (se copian a la cita)."""
    verificar_dueno(_business_id_de_zona(zone_id), user_id)
    supabase.table("home_visit_zones").delete().eq("id", zone_id).execute()
    return {"deleted": True}
