from datetime import datetime
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import Field

from services.db import (
    get_services,
    create_appointment,
    get_client_appointments,
    get_appointment_by_id_and_phone,
    update_appointment_schedule,
    get_employees,
    get_employee_by_id,
    get_employee_services,
)
from services.scheduling import (
    DIAS_SEMANA_ES,
    DIAS_MAP,
    es_hora_valida,
    hay_choque_de_horario,
    generar_horas_disponibles,
    obtener_horario_dia,
)

BusinessId = Annotated[str, InjectedState("business_id")]
ClientPhone = Annotated[Optional[str], InjectedState("client_phone")]
EmployeeId = Annotated[Optional[str], InjectedState("employee_id")]


def _formato_hora_12h(hora_24: str) -> str:
    """Convierte 'HH:MM' (24h) a texto tipo '9:00 am', para mostrarle al cliente."""
    return datetime.strptime(hora_24, "%H:%M").strftime("%I:%M %p").lstrip("0").lower()


def _formato_precio_cop(precio) -> str:
    """Formatea un precio como '$15.000' (separador de miles con punto, sin decimales)."""
    try:
        return f"${int(round(float(precio))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


@tool
def registrar_telefono_cliente(
    telefono: Annotated[str, Field(description="Numero de WhatsApp o celular que dio el cliente, en cualquier formato.")],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Guarda el numero de WhatsApp/celular del cliente para poder identificarlo
    en consultas, cancelaciones, reprogramaciones o para agendar una cita
    nueva. Es la UNICA forma de identificar al cliente: nunca pidas cedula
    ni ningun otro documento. Usa esta tool apenas el cliente te de su
    numero, antes de intentar cualquier otra accion que lo requiera.
    """
    limpio = "".join(c for c in telefono if c.isdigit())
    if len(limpio) < 7:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Ese numero no parece valido. Pidele al cliente que lo escriba de nuevo, con indicativo si es posible.",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    return Command(
        update={
            "client_phone": limpio,
            "messages": [
                ToolMessage(content=f"Numero de cliente registrado: {limpio}", tool_call_id=tool_call_id)
            ],
        }
    )


@tool
def consultar_empleados_disponibles(
    business_id: BusinessId,
    tool_call_id: Annotated[str, InjectedToolCallId],
    fecha: Annotated[
        Optional[str],
        Field(
            description=(
                "Fecha en formato YYYY-MM-DD. Si ya sabes para que dia quiere la cita el cliente, "
                "pasala aqui: filtra la lista a solo los empleados que SI trabajan ese dia de la "
                "semana (segun su propio horario). Si todavia no sabes la fecha, omite este "
                "parametro y se listan todos los empleados activos."
            )
        ),
    ] = None,
) -> Command:
    """
    Lista los empleados activos del negocio (opcionalmente filtrados a los
    que trabajan un dia especifico) con los servicios que cada uno ofrece.
    REGLA CRITICA: siempre debes usar esta tool y preguntarle al cliente con
    quien prefiere la cita ANTES de mostrar servicios, horas disponibles, o
    agendar, salvo que el empleado ya venga fijo (enlace de chat propio de
    un empleado) o el negocio solo tenga un empleado activo.
    """
    empleados = get_employees(business_id)

    if fecha:
        try:
            fecha_dt = datetime.fromisoformat(fecha)
            dia_codigo = DIAS_MAP[fecha_dt.weekday()]
            empleados = [e for e in empleados if (obtener_horario_dia(e["id"], dia_codigo) or {}).get("is_open")]
        except ValueError:
            pass  # fecha con formato invalido: mejor listar todos que fallar la tool

    resultado = {
        "empleados": [
            {
                "id": e["id"],
                "nombre": e.get("name") or "Sin nombre",
                "servicios": [s["name"] for s in get_employee_services(e["id"])],
            }
            for e in empleados
        ]
    }

    return Command(
        update={
            # Opciones de seleccion rapida para el widget de chat: el cliente
            # puede tocar un boton en vez de escribir el nombre del empleado.
            "ultimas_opciones": [
                {"label": e["nombre"], "value": e["nombre"]} for e in resultado["empleados"]
            ],
            "messages": [
                ToolMessage(
                    content=str(resultado), name="consultar_empleados_disponibles", tool_call_id=tool_call_id
                )
            ],
        }
    )


@tool
def seleccionar_empleado(
    employee_id: Annotated[str, Field(description="El id del empleado elegido (de consultar_empleados_disponibles).")],
    business_id: BusinessId,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Guarda con que empleado va a ser la cita, despues de que el cliente lo
    elija (o cuando solo hay un empleado y lo eliges automaticamente sin
    preguntar). A partir de aqui, servicios y horas disponibles se
    consultan para ese empleado especifico.
    """
    empleado = get_employee_by_id(employee_id)
    if not empleado or empleado["business_id"] != business_id or not empleado.get("active"):
        return Command(
            update={
                "messages": [
                    ToolMessage(content="Ese empleado no existe o ya no esta disponible.", tool_call_id=tool_call_id)
                ]
            }
        )

    return Command(
        update={
            "employee_id": employee_id,
            "messages": [
                ToolMessage(
                    content=f"Empleado seleccionado: {empleado.get('name') or employee_id}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def consultar_servicios_disponibles(
    business_id: BusinessId,
    employee_id: EmployeeId,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Consulta los servicios activos que ofrece el negocio (o, si ya se
    selecciono un empleado, solo los que ESE empleado ofrece), con precio
    y duracion.
    """
    servicios = get_employee_services(employee_id) if employee_id else get_services(business_id)
    resultado = {"servicios": servicios}

    return Command(
        update={
            # Opciones de seleccion rapida: el cliente toca el servicio en vez
            # de escribir su nombre exacto.
            "ultimas_opciones": [
                {
                    "label": f"{s['name']} · {s['duration_minutes']} min · {_formato_precio_cop(s['price'])}",
                    "value": s["name"],
                }
                for s in servicios
            ],
            "messages": [
                ToolMessage(
                    content=str(resultado), name="consultar_servicios_disponibles", tool_call_id=tool_call_id
                )
            ],
        }
    )


@tool
def consultar_horas_disponibles(
    fecha: Annotated[str, Field(description="Fecha en formato YYYY-MM-DD (la hora se ignora, usa 00:00:00).")],
    business_id: BusinessId,
    employee_id: EmployeeId,
    tool_call_id: Annotated[str, InjectedToolCallId],
    servicio_nombre: Annotated[
        Optional[str], Field(description="Nombre del servicio, para calcular la duracion correcta. Opcional.")
    ] = None,
) -> Command:
    """
    Consulta las horas realmente disponibles (libres) de un dia especifico
    PARA EL EMPLEADO YA SELECCIONADO, considerando su horario de trabajo,
    su almuerzo y sus citas ya agendadas (la agenda de cada empleado es
    independiente). Usa esto cuando el cliente pregunte que horas hay
    disponibles, o antes de sugerir una hora para agendar. Si todavia no
    hay un empleado seleccionado, esta tool devuelve un error: primero usa
    consultar_empleados_disponibles y seleccionar_empleado.
    """
    if not employee_id:
        error = {"error": "Primero hay que saber con que empleado es la cita. Usa consultar_empleados_disponibles y seleccionar_empleado."}
        return Command(update={"messages": [ToolMessage(content=str(error), tool_call_id=tool_call_id)]})

    duracion = 30
    if servicio_nombre:
        servicios = get_employee_services(employee_id)
        servicio = next((s for s in servicios if s["name"].lower() == servicio_nombre.lower()), None)
        if servicio:
            duracion = servicio.get("duration_minutes", 30)

    fecha_iso = f"{fecha}T00:00:00"
    fecha_dt = datetime.fromisoformat(fecha_iso)
    dias_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    dia_codigo = dias_map[fecha_dt.weekday()]
    horario_dia = obtener_horario_dia(employee_id, dia_codigo)
    negocio_abierto_ese_dia = bool(horario_dia and horario_dia.get("is_open"))

    horas_libres = generar_horas_disponibles(business_id, employee_id, fecha_iso, duracion)
    resultado = {
        "horas_disponibles": horas_libres,
        # La IA necesita esto para poder explicarle al cliente POR QUE no hay
        # horas (el empleado no trabaja ese dia vs. simplemente todo ocupado),
        # en vez de adivinar o saltar de dia en silencio.
        "negocio_abierto_ese_dia": negocio_abierto_ese_dia,
        "dia_semana": DIAS_SEMANA_ES[dia_codigo],
    }

    return Command(
        update={
            # Opciones de seleccion rapida: el cliente toca la hora en vez de
            # escribirla. Se ofrecen todas las horas libres (el texto del
            # mensaje puede curar una sublista mas corta, pero los botones
            # dan la disponibilidad real completa).
            "ultimas_opciones": [
                {"label": _formato_hora_12h(h), "value": _formato_hora_12h(h)} for h in horas_libres
            ],
            "messages": [
                ToolMessage(content=str(resultado), name="consultar_horas_disponibles", tool_call_id=tool_call_id)
            ],
        }
    )


@tool
def crear_cita(
    servicio_nombre: Annotated[str, Field(description="Nombre exacto del servicio a agendar.")],
    fecha_hora: Annotated[str, Field(description="Fecha y hora en formato ISO 8601, ej: 2026-06-22T15:00:00")],
    nombre_cliente: Annotated[str, Field(description="Nombre del cliente.")],
    business_id: BusinessId,
    client_phone: ClientPhone,
    employee_id: EmployeeId,
) -> dict:
    """Crea una cita nueva para el cliente, validando horario y disponibilidad del empleado seleccionado."""
    if not client_phone:
        return {"error": "Antes de agendar necesito el numero de WhatsApp o celular del cliente. Pidelo y usa registrar_telefono_cliente."}
    if not employee_id:
        return {"error": "Antes de agendar necesito saber con que empleado es la cita. Usa consultar_empleados_disponibles y seleccionar_empleado."}

    es_valida, mensaje_error = es_hora_valida(fecha_hora, employee_id)
    if not es_valida:
        return {"error": mensaje_error}

    servicios = get_employee_services(employee_id)
    servicio = next((s for s in servicios if s["name"].lower() == servicio_nombre.lower()), None)
    if not servicio:
        return {"error": f"No encontre el servicio '{servicio_nombre}' para ese empleado. Servicios disponibles: {[s['name'] for s in servicios]}"}

    fecha_hora_dt = datetime.fromisoformat(fecha_hora)
    duracion = servicio.get("duration_minutes", 30)

    hay_choque, mensaje_choque = hay_choque_de_horario(business_id, employee_id, fecha_hora_dt, duracion)
    if hay_choque:
        return {"error": mensaje_choque}

    resultado = create_appointment(
        business_id=business_id,
        client_phone=client_phone,
        client_name=nombre_cliente,
        service_id=servicio["id"],
        scheduled_at=fecha_hora,
        employee_id=employee_id,
    )

    try:
        from services.db import get_business_by_id
        from services.push_notifications import enviar_notificacion_nueva_cita
        from services.scheduling import formatear_fecha_natural

        business = get_business_by_id(business_id)
        enviar_notificacion_nueva_cita(
            fcm_token=business.get("fcm_token") if business else None,
            nombre_cliente=nombre_cliente,
            servicio=servicio["name"],
            fecha_hora_texto=formatear_fecha_natural(fecha_hora_dt),
        )
    except Exception as e:
        print(f"No se pudo enviar la notificacion push: {e}")

    if resultado:
        from services.realtime import emitir_evento_cita

        emitir_evento_cita("appointment.created", business_id, resultado[0]["id"])

    return {"cita_creada": resultado}


@tool
def consultar_citas_cliente(business_id: BusinessId, client_phone: ClientPhone) -> dict:
    """Consulta las citas confirmadas del cliente actual."""
    if not client_phone:
        return {"error": "Necesito el numero de WhatsApp o celular del cliente para buscar sus citas. Pidelo y usa registrar_telefono_cliente."}
    return {"citas": get_client_appointments(business_id, client_phone)}


@tool
def cancelar_cita(
    appointment_id: Annotated[str, Field(description="ID de la cita a cancelar.")],
    business_id: BusinessId,
    client_phone: ClientPhone,
) -> dict:
    """Cancela una cita existente del cliente."""
    if not client_phone:
        return {"error": "Necesito el numero de WhatsApp o celular del cliente para poder cancelar. Pidelo y usa registrar_telefono_cliente."}

    cita = get_appointment_by_id_and_phone(appointment_id, client_phone)
    if not cita:
        return {"error": "No encontre esa cita para este cliente."}

    from services.db import cancel_appointment

    resultado = cancel_appointment(appointment_id, client_phone)

    try:
        from services.push_notifications import enviar_notificacion_cita_cancelada
        from services.scheduling import formatear_fecha_natural
        from services.db import get_business_by_id

        business = get_business_by_id(business_id)
        fecha_hora_dt = datetime.fromisoformat(cita["scheduled_at"])
        nombre_servicio = cita.get("services", {}).get("name", "su cita") if cita.get("services") else "su cita"
        enviar_notificacion_cita_cancelada(
            fcm_token=business.get("fcm_token") if business else None,
            nombre_cliente=cita.get("client_name") or "Cliente",
            servicio=nombre_servicio,
            fecha_hora_texto=formatear_fecha_natural(fecha_hora_dt),
        )
    except Exception as e:
        print(f"No se pudo enviar la notificacion de cancelacion: {e}")

    from services.realtime import emitir_evento_cita

    emitir_evento_cita("appointment.cancelled", business_id, appointment_id)

    return {"resultado": resultado}


@tool
def reprogramar_cita(
    appointment_id: Annotated[str, Field(description="ID de la cita a reprogramar.")],
    nueva_fecha_hora: Annotated[str, Field(description="Nueva fecha y hora en formato ISO 8601, ej: 2026-07-01T17:00:00")],
    business_id: BusinessId,
    client_phone: ClientPhone,
) -> dict:
    """
    Cambia la fecha y/u hora de una cita existente del cliente, manteniendo
    el mismo servicio. Usa esto cuando el cliente pida mover, cambiar, o
    reagendar una cita que ya tiene, en vez de cancelarla y crear una nueva.
    """
    if not client_phone:
        return {"error": "Necesito el numero de WhatsApp o celular del cliente para reprogramar. Pidelo y usa registrar_telefono_cliente."}

    cita_actual = get_appointment_by_id_and_phone(appointment_id, client_phone)
    if not cita_actual:
        return {"error": "No encontre esa cita para este cliente."}

    empleado_id_cita = cita_actual["employee_id"]
    fecha_hora_anterior = datetime.fromisoformat(cita_actual["scheduled_at"])
    duracion = cita_actual.get("services", {}).get("duration_minutes", 30) if cita_actual.get("services") else 30
    nombre_servicio = cita_actual.get("services", {}).get("name", "tu cita") if cita_actual.get("services") else "tu cita"

    es_valida, mensaje_error = es_hora_valida(nueva_fecha_hora, empleado_id_cita)
    if not es_valida:
        return {"error": mensaje_error}

    nueva_fecha_hora_dt = datetime.fromisoformat(nueva_fecha_hora)

    hay_choque, mensaje_choque = hay_choque_de_horario(
        business_id, empleado_id_cita, nueva_fecha_hora_dt, duracion, ignorar_appointment_id=appointment_id
    )
    if hay_choque:
        return {"error": mensaje_choque}

    update_appointment_schedule(appointment_id, nueva_fecha_hora)

    try:
        from services.push_notifications import enviar_notificacion_cita_reprogramada
        from services.scheduling import formatear_fecha_natural
        from services.db import get_business_by_id

        business = get_business_by_id(business_id)
        enviar_notificacion_cita_reprogramada(
            fcm_token=business.get("fcm_token") if business else None,
            nombre_cliente=cita_actual.get("client_name") or "Cliente",
            servicio=nombre_servicio,
            fecha_anterior_texto=formatear_fecha_natural(fecha_hora_anterior),
            fecha_nueva_texto=formatear_fecha_natural(nueva_fecha_hora_dt),
        )
    except Exception as e:
        print(f"No se pudo enviar la notificacion de reprogramacion: {e}")

    from services.realtime import emitir_evento_cita

    emitir_evento_cita("appointment.updated", business_id, appointment_id)

    return {"cita_reprogramada": True, "nueva_fecha_hora": nueva_fecha_hora}


@tool
def transferir_a_equipo(tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """
    Usa esta tool INMEDIATAMENTE cuando el cliente pida explicitamente
    hablar con una persona real, con el equipo, con el dueño del negocio, o
    con alguien por su nombre propio, en cualquier momento de la
    conversacion (no solo al inicio). Esto detiene al bot para que un
    humano tome la conversacion.
    """
    return Command(
        update={
            "transferido": True,
            "messages": [
                ToolMessage(content="Cliente transferido a un humano del equipo.", tool_call_id=tool_call_id)
            ],
        }
    )


TOOLS = [
    registrar_telefono_cliente,
    consultar_empleados_disponibles,
    seleccionar_empleado,
    consultar_servicios_disponibles,
    consultar_horas_disponibles,
    crear_cita,
    consultar_citas_cliente,
    cancelar_cita,
    reprogramar_cita,
    transferir_a_equipo,
]
