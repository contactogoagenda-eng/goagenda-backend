from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.auth import obtener_usuario_actual, verificar_acceso_empleado, verificar_dueno
from services.db import (
    get_employees,
    get_employee_by_id,
    update_employee,
    get_employee_hours,
    upsert_employee_hours,
    get_employee_services,
    set_employee_services,
)

router = APIRouter(tags=["employees"])

DIAS_VALIDOS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@router.get("/employees")
def listar_empleados(business_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Lista los empleados de un negocio (el dueño incluido, role='owner')."""
    verificar_dueno(business_id, user_id)
    return {"employees": get_employees(business_id)}


class EmployeeUpdate(BaseModel):
    name: str


@router.put("/employees/{employee_id}")
def actualizar_empleado(employee_id: str, data: EmployeeUpdate, user_id: str = Depends(obtener_usuario_actual)):
    """Actualiza el nombre de un empleado. Lo puede hacer el dueño del negocio, o el propio empleado."""
    verificar_acceso_empleado(employee_id, user_id)
    nombre = data.name.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacio")
    empleado = update_employee(employee_id, {"name": nombre})
    if not empleado:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    return {"employee": empleado}


@router.delete("/employees/{employee_id}")
def eliminar_empleado(employee_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    'Elimina' un empleado mediante borrado logico (lo marca inactivo), para
    no romper el historial de citas que ya atendio. Solo el dueño del
    negocio puede hacerlo, y no se puede desactivar al empleado principal
    (role='owner': el dueño siempre necesita su propia agenda activa).
    """
    empleado = get_employee_by_id(employee_id)
    if not empleado:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    verificar_dueno(empleado["business_id"], user_id)
    if empleado["role"] == "owner":
        raise HTTPException(status_code=400, detail="No se puede eliminar al empleado principal (el dueño)")

    resultado = update_employee(employee_id, {"active": False})
    return {"deleted": True, "employee": resultado}


@router.get("/employees/{employee_id}/hours")
def obtener_horario_empleado(employee_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """Horario de trabajo propio de un empleado (independiente del de otros empleados)."""
    verificar_acceso_empleado(employee_id, user_id)
    horario = get_employee_hours(employee_id)
    dias_dict = {row["day"]: row for row in horario}
    return {"employee_hours": [dias_dict[d] for d in DIAS_VALIDOS if d in dias_dict]}


class EmployeeHourUpdate(BaseModel):
    day: str
    is_open: bool
    opening_time: Optional[str] = None
    closing_time: Optional[str] = None
    lunch_start: Optional[str] = None
    lunch_end: Optional[str] = None


@router.put("/employees/{employee_id}/hours")
def actualizar_horario_empleado(employee_id: str, data: EmployeeHourUpdate, user_id: str = Depends(obtener_usuario_actual)):
    """Crea o actualiza el horario de un dia especifico para un empleado."""
    verificar_acceso_empleado(employee_id, user_id)
    if data.day not in DIAS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Dia invalido. Usa uno de: {DIAS_VALIDOS}")

    campos = {"is_open": data.is_open, "lunch_start": data.lunch_start, "lunch_end": data.lunch_end}
    if data.opening_time:
        campos["opening_time"] = data.opening_time
    if data.closing_time:
        campos["closing_time"] = data.closing_time

    resultado = upsert_employee_hours(employee_id, data.day, campos)
    return {"employee_hour": resultado}


def _verificar_dueno_del_empleado(employee_id: str, user_id: str) -> str:
    """A que servicios queda ligado un empleado lo decide el negocio, no el propio empleado."""
    empleado = get_employee_by_id(employee_id)
    if not empleado:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    verificar_dueno(empleado["business_id"], user_id)
    return empleado["business_id"]


@router.get("/employees/{employee_id}/services")
def obtener_servicios_empleado(employee_id: str, user_id: str = Depends(obtener_usuario_actual)):
    """
    Servicios a los que esta ligado un empleado (los que ofrece / puede agendar).
    Lectura permitida al dueño y al propio empleado (solo lectura: a que
    servicios queda ligado lo sigue decidiendo el dueño via el PUT de abajo).
    """
    verificar_acceso_empleado(employee_id, user_id)
    return {"services": get_employee_services(employee_id)}


class EmployeeServicesUpdate(BaseModel):
    service_ids: list[str]


@router.put("/employees/{employee_id}/services")
def actualizar_servicios_empleado(
    employee_id: str, data: EmployeeServicesUpdate, user_id: str = Depends(obtener_usuario_actual)
):
    """Reemplaza por completo la lista de servicios a los que esta ligado un empleado (lo decide el dueño del negocio)."""
    business_id = _verificar_dueno_del_empleado(employee_id, user_id)
    set_employee_services(employee_id, data.service_ids)
    return {"services": get_employee_services(employee_id), "business_id": business_id}
