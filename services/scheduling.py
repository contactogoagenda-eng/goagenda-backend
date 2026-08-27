from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.db import get_confirmed_appointments_for_day, get_employee_hours_for_day

TZ_NEGOCIO = ZoneInfo("America/Bogota")

DIAS_SEMANA_ES = {
    "mon": "lunes", "tue": "martes", "wed": "miercoles",
    "thu": "jueves", "fri": "viernes", "sat": "sabado", "sun": "domingo",
}

DIAS_MAP = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

DIA_SEMANA_COMPLETO_ES = {
    0: "lunes", 1: "martes", 2: "miercoles", 3: "jueves", 4: "viernes", 5: "sabado", 6: "domingo",
}


def ahora_local() -> datetime:
    """
    Hora actual en la zona horaria de Colombia, como datetime naive.
    El servidor (Railway) corre en UTC, asi que datetime.now() sin esto
    ya esta "en el dia siguiente" desde las 7pm hora Colombia en adelante,
    haciendo que 'hoy'/'mañana' salgan mal para el cliente. Se devuelve
    naive (sin tzinfo) para que siga siendo compatible con el resto de
    fechas (horarios de atencion, fecha_hora del cliente, etc.), que
    tambien son naive y se asumen siempre en hora de Colombia.
    """
    return datetime.now(TZ_NEGOCIO).replace(tzinfo=None)


def formatear_fecha_natural(fecha_hora: datetime) -> str:
    """
    Convierte una fecha/hora a texto natural en espanol para notificaciones:
    'hoy a las 3:00 pm', 'mañana a las 9:00 am', o
    'miercoles 2 de julio a las 5:00 pm' para fechas mas lejanas.
    """
    hoy = ahora_local().date()
    fecha = fecha_hora.date()
    dias_diferencia = (fecha - hoy).days

    hora_texto = fecha_hora.strftime("%I:%M %p").lstrip("0").lower()

    if dias_diferencia == 0:
        return f"hoy a las {hora_texto}"
    elif dias_diferencia == 1:
        return f"mañana a las {hora_texto}"
    else:
        dia_semana = DIA_SEMANA_COMPLETO_ES[fecha_hora.weekday()]
        mes = MESES_ES[fecha_hora.month]
        return f"{dia_semana} {fecha_hora.day} de {mes} a las {hora_texto}"


def obtener_horario_dia(employee_id: str, dia_codigo: str):
    """
    Horario de trabajo de un empleado especifico para un dia de la semana.
    Cada empleado tiene su propia agenda y horario (independiente del de
    otros empleados del mismo negocio).
    """
    return get_employee_hours_for_day(employee_id, dia_codigo)


def generar_horas_disponibles(business_id: str, employee_id: str, fecha_str: str, duracion_minutos: int = 30) -> list[str]:
    """
    Calcula los horarios libres de un empleado especifico en un dia dado,
    en intervalos de 30 minutos, excluyendo: dias que no trabaja, su
    horario de almuerzo, y sus citas ya confirmadas (no las de otros
    empleados del mismo negocio, que tienen su propia agenda).
    """
    fecha_dt = datetime.fromisoformat(fecha_str)
    dias_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    dia_codigo = dias_map[fecha_dt.weekday()]

    horario_dia = obtener_horario_dia(employee_id, dia_codigo)
    if not horario_dia or not horario_dia.get("is_open"):
        return []

    opening = horario_dia.get("opening_time", "09:00:00")
    closing = horario_dia.get("closing_time", "19:00:00")
    lunch_start = horario_dia.get("lunch_start")
    lunch_end = horario_dia.get("lunch_end")

    hora_apertura = datetime.combine(fecha_dt.date(), datetime.strptime(opening, "%H:%M:%S").time())
    hora_cierre = datetime.combine(fecha_dt.date(), datetime.strptime(closing, "%H:%M:%S").time())

    citas_del_dia = get_confirmed_appointments_for_day(business_id, fecha_dt.strftime("%Y-%m-%d"), employee_id)
    intervalos_ocupados = []
    for cita in citas_del_dia:
        inicio_cita = datetime.fromisoformat(cita["scheduled_at"])
        duracion_cita = 30
        if cita.get("services") and cita["services"].get("duration_minutes"):
            duracion_cita = cita["services"]["duration_minutes"]
        intervalos_ocupados.append((inicio_cita, inicio_cita + timedelta(minutes=duracion_cita)))

    ahora = ahora_local()
    horas_libres = []
    candidato = hora_apertura

    while candidato + timedelta(minutes=duracion_minutos) <= hora_cierre:
        fin_candidato = candidato + timedelta(minutes=duracion_minutos)

        # Si el dia es hoy, no ofrecer horas que ya pasaron
        if candidato < ahora:
            candidato += timedelta(minutes=30)
            continue

        # Excluir horario de almuerzo
        en_almuerzo = False
        if lunch_start and lunch_end:
            hora_str = candidato.strftime("%H:%M:%S")
            if lunch_start <= hora_str < lunch_end:
                en_almuerzo = True

        # Excluir si choca con alguna cita existente
        choca = any(candidato < fin_oc and fin_candidato > inicio_oc for inicio_oc, fin_oc in intervalos_ocupados)

        if not en_almuerzo and not choca:
            horas_libres.append(candidato.strftime("%H:%M"))

        candidato += timedelta(minutes=30)

    return horas_libres


def es_hora_valida(fecha_hora_str: str, employee_id: str) -> tuple[bool, str]:
    try:
        fecha_hora = datetime.fromisoformat(fecha_hora_str)
    except ValueError:
        return False, "El formato de fecha y hora no es valido."

    dias_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    dia_codigo = dias_map[fecha_hora.weekday()]

    horario_dia = obtener_horario_dia(employee_id, dia_codigo)

    if not horario_dia or not horario_dia.get("is_open"):
        return False, f"No atiende los {DIAS_SEMANA_ES[dia_codigo]}."

    opening = horario_dia.get("opening_time", "09:00:00")
    closing = horario_dia.get("closing_time", "19:00:00")
    lunch_start = horario_dia.get("lunch_start")
    lunch_end = horario_dia.get("lunch_end")

    hora_str = fecha_hora.strftime("%H:%M:%S")

    if hora_str < opening or hora_str >= closing:
        return False, f"Esa hora esta fuera del horario de atencion ({opening[:5]} a {closing[:5]}) para los {DIAS_SEMANA_ES[dia_codigo]}."

    if lunch_start and lunch_end and lunch_start <= hora_str < lunch_end:
        return False, f"Esa hora cae en el horario de almuerzo ({lunch_start[:5]} a {lunch_end[:5]}). Por favor elige otra hora."

    return True, ""


def hay_choque_de_horario(
    business_id: str,
    employee_id: str,
    fecha_hora: datetime,
    duracion_minutos: int,
    ignorar_appointment_id: str | None = None,
) -> tuple[bool, str]:
    """Choque de horario dentro de la agenda de UN empleado; otro empleado del mismo negocio si puede tener una cita a la misma hora."""
    fecha_str = fecha_hora.strftime("%Y-%m-%d")
    nuevo_inicio = fecha_hora
    nuevo_fin = fecha_hora + timedelta(minutes=duracion_minutos)

    citas_del_dia = get_confirmed_appointments_for_day(business_id, fecha_str, employee_id)

    for cita in citas_del_dia:
        if ignorar_appointment_id and cita.get("id") == ignorar_appointment_id:
            continue  # es la propia cita que estamos reprogramando, no cuenta como choque

        cita_inicio = datetime.fromisoformat(cita["scheduled_at"])
        duracion_existente = 30
        if cita.get("services") and cita["services"].get("duration_minutes"):
            duracion_existente = cita["services"]["duration_minutes"]
        cita_fin = cita_inicio + timedelta(minutes=duracion_existente)

        if nuevo_inicio < cita_fin and nuevo_fin > cita_inicio:
            hora_choque = cita_inicio.strftime("%H:%M")
            hora_fin_choque = cita_fin.strftime("%H:%M")
            return True, f"Ya hay una cita agendada de {hora_choque} a {hora_fin_choque}. Por favor elige otra hora."

    return False, ""
