from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.auth import requiere_super_admin
from services.db import supabase
from services.invitation_codes import crear_codigo_invitacion

router = APIRouter(prefix="/admin", tags=["super-admin"])


class BusinessCreate(BaseModel):
    name: str
    business_type: str | None = None


@router.post("/businesses")
def crear_negocio(data: BusinessCreate, user_id: str = Depends(requiere_super_admin)):
    """
    Crea un negocio SIN dueño (lo pone el super admin). El dueño real
    queda ligado despues, cuando valide el codigo de invitacion tipo
    'admin' que se genera para este negocio (ver POST
    /admin/businesses/{business_id}/invitation-codes).
    """
    nombre = data.name.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre del negocio es obligatorio")

    nuevo = {"name": nombre, "reminder_hours_before": 24}
    if data.business_type and data.business_type.strip():
        nuevo["business_type"] = data.business_type.strip()

    response = supabase.table("businesses").insert(nuevo).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="No se pudo crear el negocio")
    business = response.data[0]

    # Horario general por defecto (lunes a sabado 9am-7pm, domingo cerrado);
    # el horario real de agendamiento lo define cada empleado cuando se crea.
    try:
        filas_horario = [
            {
                "business_id": business["id"],
                "day": dia,
                "is_open": True,
                "opening_time": "09:00",
                "closing_time": "19:00",
            }
            for dia in ["mon", "tue", "wed", "thu", "fri", "sat"]
        ]
        filas_horario.append({"business_id": business["id"], "day": "sun", "is_open": False})
        supabase.table("business_hours").insert(filas_horario).execute()
    except Exception as e:
        print(f"No se pudo crear el horario por defecto del negocio {business['id']}: {e}")

    return {"business": business}


@router.get("/businesses")
def listar_negocios(user_id: str = Depends(requiere_super_admin)):
    """Lista todos los negocios, para que el super admin los administre y los bloquee si hace falta."""
    response = supabase.table("businesses").select("*").order("created_at", desc=True).execute()
    return {"businesses": response.data}


class BlockedUpdate(BaseModel):
    blocked: bool


@router.put("/businesses/{business_id}/blocked")
def actualizar_bloqueo_negocio(business_id: str, data: BlockedUpdate, user_id: str = Depends(requiere_super_admin)):
    """
    Bloquea o desbloquea un negocio: mientras esta bloqueado, su chat
    publico deja de agendar y el dueño no puede administrarlo desde la
    app (ver services/auth.py:verificar_dueno).
    """
    response = supabase.table("businesses").update({"blocked": data.blocked}).eq("id", business_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    return {"business": response.data[0]}


@router.post("/businesses/{business_id}/invitation-codes")
def crear_codigo_admin(business_id: str, user_id: str = Depends(requiere_super_admin)):
    """
    Genera un codigo de invitacion para que el futuro dueño de este
    negocio lo valide (POST /invitation-codes/redeem) y quede ligado como
    su administrador. Se puede llamar varias veces si el codigo se pierde
    o vence sin usarse; cada llamada genera uno nuevo.
    """
    negocio = supabase.table("businesses").select("id").eq("id", business_id).execute()
    if not negocio.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    codigo = crear_codigo_invitacion(business_id, role="admin", created_by=user_id)
    return {"invitation_code": codigo}
