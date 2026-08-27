from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.auth import obtener_usuario_actual, verificar_dueno
from services.db import create_employee, supabase
from services.invitation_codes import crear_codigo_invitacion, marcar_codigo_usado, obtener_codigo_sin_usar

router = APIRouter(tags=["invitations"])


class EmployeeInvitationCreate(BaseModel):
    employee_name: str | None = None


@router.post("/businesses/{business_id}/invitation-codes")
def crear_codigo_empleado(business_id: str, data: EmployeeInvitationCreate, user_id: str = Depends(obtener_usuario_actual)):
    """
    El dueño del negocio genera un codigo para invitar a un empleado suyo.
    El empleado se registra en la app y valida este codigo (POST
    /invitation-codes/redeem) para quedar ligado a este negocio.
    """
    verificar_dueno(business_id, user_id)
    codigo = crear_codigo_invitacion(
        business_id, role="employee", created_by=user_id, employee_name=data.employee_name
    )
    return {"invitation_code": codigo}


class RedeemInput(BaseModel):
    code: str


@router.post("/invitation-codes/redeem")
def redimir_codigo_invitacion(data: RedeemInput, user_id: str = Depends(obtener_usuario_actual)):
    """
    Valida un codigo de invitacion despues de que el usuario ya se
    registro en Supabase Auth (email/password). Segun el rol del codigo:
    - 'admin': liga este usuario como dueño del negocio (solo si todavia
      no tiene uno) y le crea su propio registro de empleado principal.
    - 'employee': crea el registro de empleado normal ligado al negocio.
    En ambos casos el empleado nuevo queda con un horario por defecto
    (ver services/db.py:create_employee) para poder recibir citas de una vez.
    """
    codigo = obtener_codigo_sin_usar(data.code)
    if not codigo:
        raise HTTPException(status_code=404, detail="Codigo invalido o ya usado")

    business_id = codigo["business_id"]

    if codigo["role"] == "admin":
        negocio = supabase.table("businesses").select("owner_id").eq("id", business_id).execute()
        if not negocio.data:
            raise HTTPException(status_code=404, detail="Negocio no encontrado")
        if negocio.data[0].get("owner_id"):
            raise HTTPException(status_code=409, detail="Este negocio ya tiene un dueño asignado")

        supabase.table("businesses").update({"owner_id": user_id}).eq("id", business_id).execute()
        empleado = create_employee(business_id, user_id, name=None, role="owner")
    else:
        empleado = create_employee(business_id, user_id, name=codigo.get("employee_name"), role="staff")

    marcar_codigo_usado(codigo["id"], user_id)
    return {"business_id": business_id, "role": codigo["role"], "employee": empleado}
