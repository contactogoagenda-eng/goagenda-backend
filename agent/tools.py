import re
import unicodedata
from datetime import datetime
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import Field

from services.db import (
    get_home_visit_zones,
    get_services,
    get_client_appointments,
    get_appointment_by_id_and_phone,
    update_appointment_schedule,
    get_employees,
    get_employee_by_id,
    get_employee_services,
    get_business_by_id,
)
from services.push_notifications import enviar_notificacion_escalamiento
from services.realtime import gestor_tiempo_real
from services.scheduling import (
    DIAS_SEMANA_ES,
    DIAS_MAP,
    ahora_local,
    es_hora_valida,
    hay_choque_de_horario,
    generar_horas_disponibles,
    obtener_horario_dia,
    formatear_fecha_natural,
)
from services.whatsapp import normalizar_numero_whatsapp

BusinessId = Annotated[str, InjectedState("business_id")]
SessionId = Annotated[str, InjectedState("session_id")]
ClientPhone = Annotated[Optional[str], InjectedState("client_phone")]
EmployeeId = Annotated[Optional[str], InjectedState("employee_id")]
EmployeeFijo = Annotated[bool, InjectedState("employee_fijo")]


def _formato_hora_12h(hora_24: str) -> str:
    """Convierte 'HH:MM' (24h) a texto tipo '9:00 am', para mostrarle al cliente."""
    return datetime.strptime(hora_24, "%H:%M").strftime("%I:%M %p").lstrip("0").lower()


def _muestra_repartida(items: list[str], n: int) -> list[str]:
    """Hasta n elementos de la lista repartidos parejo (incluye el primero y el ultimo)."""
    if len(items) <= n:
        return list(items)
    pasos = n - 1
    return [items[round(i * (len(items) - 1) / pasos)] for i in range(n)]


def _domicilios_activos(business_id: str) -> bool:
    """Flag del negocio: con los domicilios desactivados el chat se comporta como antes (solo en el local)."""
    negocio = get_business_by_id(business_id)
    return bool(negocio and negocio.get("home_visits_enabled"))


def _validar_domicilio(business_id: str, servicio: dict | None, direccion: str | None, mensajes: list) -> str | None:
    """
    Valida una cita a domicilio (direccion presente). Devuelve un mensaje de
    error, o None si esta bien. Solo se puede pedir domicilio en servicios que
    lo permiten, y la direccion debe salir de lo que el cliente escribio: el
    modelo llego a inventar datos (ej. un telefono) que el cliente nunca dio.
    """
    if not direccion or not direccion.strip():
        return None
    if not _domicilios_activos(business_id):
        return "Este negocio no hace domicilios. La cita solo puede ser en el local; no vuelvas a ofrecer domicilio."
    if servicio and not servicio.get("offers_home_visit"):
        return "Ese servicio no se presta a domicilio. Ofrecele agendarlo en el local."

    def _tokens(texto: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9áéíóúñ]+", texto.lower()) if len(t) >= 2}

    texto_cliente = " ".join(str(m.content) for m in mensajes if getattr(m, "type", None) == "human")
    tokens_direccion = _tokens(direccion)
    if tokens_direccion and len(tokens_direccion & _tokens(texto_cliente)) / len(tokens_direccion) < 0.7:
        return "El cliente todavia no ha dado esa direccion. NO la inventes: pidele la direccion completa donde debemos ir."
    return None


def _normalizar(texto: str) -> str:
    """Minusculas y sin tildes, para comparar nombres de zonas con lo que escribio el cliente."""
    sin_tildes = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").lower().strip()


def _resolver_zona(business_id: str, direccion: str | None, zona: str | None, mensajes: list) -> tuple[dict | None, str | None]:
    """
    Politica de domicilios del negocio: valida que el lugar del cliente este
    dentro de las zonas donde se hacen domicilios y devuelve (zona, error).
    - Sin zonas configuradas: domicilio libre, sin recargo (zona None, sin error).
    - Con zonas: el nombre de una zona activa debe aparecer en lo que escribio
      el cliente (direccion o mensajes); no se acepta una zona que solo dijo el modelo.
    """
    if not direccion or not direccion.strip():
        return None, None

    zonas = get_home_visit_zones(business_id)
    if not zonas:
        return None, None

    texto_cliente = _normalizar(
        direccion + " " + " ".join(str(m.content) for m in mensajes if getattr(m, "type", None) == "human")
    )
    listado = ", ".join(f"{z['name']} (recargo {_formato_precio_cop(z['fee'])})" for z in zonas)

    if zona and zona.strip():
        elegida = next((z for z in zonas if _normalizar(z["name"]) == _normalizar(zona)), None)
        if not elegida:
            return None, (
                f"Ese lugar esta fuera de la zona de cobertura de domicilios. Zonas donde SI se llega: {listado}. "
                "Explicaselo al cliente y ofrecele agendar en el local o en una de esas zonas."
            )
    else:
        coincidencias = [z for z in zonas if _normalizar(z["name"]) in texto_cliente]
        if not coincidencias:
            return None, (
                "No puedo confirmar si esa direccion esta dentro de la cobertura de domicilios. "
                f"Pregunta al cliente en que municipio/zona esta. Zonas donde SI se llega: {listado}."
            )
        elegida = coincidencias[0]

    if _normalizar(elegida["name"]) not in texto_cliente:
        return None, (
            "El cliente todavia no ha dicho en que municipio/zona esta. NO lo supongas: preguntaselo. "
            f"Zonas donde SI se llega: {listado}."
        )
    return elegida, None


_DIAS_MESES = (
    r"lunes|martes|miercoles|jueves|viernes|sabado|domingo|"
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
)
_PATRON_FECHA = re.compile(
    rf"\b(hoy|manana|pasado manana|semana|{_DIAS_MESES})\b"  # palabras de fecha
    r"|\b\d{4}-\d{2}-\d{2}\b"  # 2026-09-21
    r"|\b\d{1,2}/\d{1,2}\b"  # 21/09
    r"|\b(el|dia)\s+\d{1,2}\b"  # el 21, dia 21
)


def _cliente_dio_fecha(mensajes: list) -> bool:
    """
    True si en algun mensaje del cliente hay una referencia a una fecha (hoy,
    mañana, un dia de la semana, un mes, "el 21", 21/09...). Guardia contra
    alucinaciones: el modelo llego a elegir una fecha por su cuenta
    (ej. "lunes 21") sin que el cliente la dijera. Las direcciones
    ("calle 24 nro 15-18") no cuentan como fecha.
    """
    texto = _normalizar(" ".join(str(m.content) for m in mensajes if getattr(m, "type", None) == "human"))
    return bool(_PATRON_FECHA.search(texto))


MENSAJE_FALTA_FECHA = (
    "El cliente todavia NO ha dicho para que dia quiere la cita. NO elijas ni supongas una fecha: "
    "preguntale para que dia le gustaria (ej: hoy, mañana, o una fecha) y espera su respuesta."
)


def _formato_precio_cop(precio) -> str:
    """Formatea un precio como '$15.000' (separador de miles con punto, sin decimales)."""
    try:
        return f"${int(round(float(precio))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "$0"


def _formato_telefono_visible(client_phone: str | None) -> str:
    """
    client_phone en el estado ya viene normalizado con indicativo de pais
    (ej. 573001234567, lo que necesita la API de WhatsApp para enviar
    mensajes) - para mostrarselo AL CLIENTE en un resumen, se le quita el
    indicativo para que se vea como el mismo lo escribio originalmente
    (ej. 3001234567), en vez de un formato que nunca tecleo.
    """
    if client_phone and client_phone.startswith("57") and len(client_phone) == 12:
        return client_phone[2:]
    return client_phone or ""


def _notificar_negocio_escalamiento(business_id: str, session_id: str, nombre_cliente: str) -> None:
    """
    Avisa al negocio que un cliente necesita que un humano siga la
    conversacion (el bot no entendio, o el cliente lo pidio
    explicitamente). Manda push (si el negocio tiene fcm_token) y un
    evento en tiempo real por el WebSocket del panel (si hay una pestaña
    conectada) — cualquiera de los dos que llegue le da al dueño el link
    a esta conversacion (business_id + session_id).
    """
    business = get_business_by_id(business_id)
    fcm_token = business.get("fcm_token") if business else None
    if fcm_token:
        enviar_notificacion_escalamiento(fcm_token, nombre_cliente, business_id, session_id)

    gestor_tiempo_real.emitir(
        business_id,
        {
            "type": "chat.escalated",
            "business_id": business_id,
            "session_id": session_id,
            "client_name": nombre_cliente,
        },
    )


@tool
def registrar_telefono_cliente(
    numero_whatsapp: Annotated[str, Field(description="Numero de WhatsApp que dio el cliente, en cualquier formato (con o sin indicativo +57).")],
    tool_call_id: Annotated[str, InjectedToolCallId],
    mensajes: Annotated[list, InjectedState("messages")],
) -> Command:
    """
    Guarda el numero de WhatsApp del cliente para poder identificarlo en
    consultas, cancelaciones, reprogramaciones o para agendar una cita
    nueva, y para poder enviarle la confirmacion de la cita por WhatsApp.
    Es la UNICA forma de identificar al cliente: nunca pidas cedula ni
    ningun otro documento, ni un telefono fijo — tiene que ser un numero
    de WhatsApp. Usa esta tool apenas el cliente te de su numero, antes de
    intentar cualquier otra accion que lo requiera.
    """
    numero_normalizado = normalizar_numero_whatsapp(numero_whatsapp)

    if numero_normalizado is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "Ese numero no parece un WhatsApp colombiano valido (celular de 10 digitos que empieza "
                            "en 3, con o sin el indicativo 57 adelante). Pidele al cliente que lo escriba de nuevo."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # Guardia contra alucinaciones: el modelo llego a inventar un numero
    # (ej. 3001234567) que el cliente nunca escribio. Solo se acepta si esos
    # digitos aparecen en algun mensaje del propio cliente.
    digitos_cliente = "".join(
        c for m in mensajes if getattr(m, "type", None) == "human" for c in str(m.content) if c.isdigit()
    )
    if numero_normalizado[2:] not in digitos_cliente:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "El cliente todavia no ha escrito ese numero. NO lo inventes ni lo deduzcas: "
                            "pidele que te escriba su numero de WhatsApp."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    return Command(
        update={
            "client_phone": numero_normalizado,
            "messages": [
                ToolMessage(
                    content=f"Numero de WhatsApp del cliente registrado: {numero_normalizado}", tool_call_id=tool_call_id
                )
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
    if not _domicilios_activos(business_id):
        # Domicilios desactivados: el modelo no debe ver el campo, asi no ofrece domicilio.
        servicios = [{k: v for k, v in s.items() if k != "offers_home_visit"} for s in servicios]
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
def seleccionar_servicio(
    service_id: Annotated[str, Field(description="El id del servicio elegido (de consultar_servicios_disponibles).")],
    business_id: BusinessId,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Confirma con que servicio va a ser la cita, en cuanto identifiques
    cual quiere el cliente (por nombre exacto o boton de seleccion
    rapida). Llamala INMEDIATAMENTE despues de resolver el service_id
    (en el mismo turno en que llames consultar_servicios_disponibles si
    hace falta para encontrarlo) — evita que se le vuelva a mostrar al
    cliente la lista completa de servicios como si tuviera que elegir de
    nuevo, aunque despues vuelvas a consultar el precio/duracion.
    """
    servicios = get_services(business_id)
    servicio = next((s for s in servicios if s["id"] == service_id), None)
    if not servicio or not servicio.get("active"):
        return Command(
            update={
                "messages": [
                    ToolMessage(content="Ese servicio no existe o ya no esta activo.", tool_call_id=tool_call_id)
                ]
            }
        )

    return Command(
        update={
            "service_id": service_id,
            "messages": [
                ToolMessage(
                    content=(
                        f"Servicio seleccionado: {servicio['name']} "
                        f"({servicio['duration_minutes']} min, {_formato_precio_cop(servicio['price'])})"
                    ),
                    tool_call_id=tool_call_id,
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
    mensajes: Annotated[list, InjectedState("messages")],
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

    if not _cliente_dio_fecha(mensajes):
        return Command(update={"messages": [ToolMessage(content=MENSAJE_FALTA_FECHA, tool_call_id=tool_call_id)]})

    duracion = 30
    if servicio_nombre:
        servicios = get_employee_services(employee_id)
        servicio = next((s for s in servicios if s["name"].lower() == servicio_nombre.lower()), None)
        if servicio:
            duracion = servicio.get("duration_minutes", 30)

    fecha_iso = f"{fecha}T00:00:00"
    fecha_dt = datetime.fromisoformat(fecha_iso)

    if fecha_dt.date() < ahora_local().date():
        error = {"error": "Esa fecha ya paso. Elige una fecha de hoy en adelante."}
        return Command(update={"messages": [ToolMessage(content=str(error), tool_call_id=tool_call_id)]})

    dias_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    dia_codigo = dias_map[fecha_dt.weekday()]
    horario_dia = obtener_horario_dia(employee_id, dia_codigo)
    negocio_abierto_ese_dia = bool(horario_dia and horario_dia.get("is_open"))

    horas_libres = generar_horas_disponibles(business_id, employee_id, fecha_iso, duracion)
    resultado = {
        "horas_disponibles": horas_libres,
        # Equivalencia am/pm -> 24h de CADA hora libre: evita que el modelo tenga que
        # convertir "12:30 am" (00:30) y la confunda con 12:30 (mediodia, almuerzo).
        "equivalencias_am_pm_a_24h": {_formato_hora_12h(h): h for h in horas_libres},
        # Muestra ya armada (5 horas REALES repartidas a lo largo del dia) que el modelo
        # debe mostrar tal cual: cuando la armaba el, llegaba a inventar rangos (ej. 9-11 am)
        # que no correspondian a la disponibilidad real.
        "muestra_para_mostrar": [_formato_hora_12h(h) for h in _muestra_repartida(horas_libres, 5)],
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
def pedir_confirmacion_cita(
    servicio_nombre: Annotated[str, Field(description="Nombre exacto del servicio a agendar.")],
    fecha_hora: Annotated[str, Field(description="Fecha y hora en formato ISO 8601, ej: 2026-06-22T15:00:00")],
    nombre_cliente: Annotated[str, Field(description="Nombre del cliente.")],
    business_id: BusinessId,
    employee_id: EmployeeId,
    client_phone: ClientPhone,
    tool_call_id: Annotated[str, InjectedToolCallId],
    mensajes: Annotated[list, InjectedState("messages")],
    direccion: Annotated[
        Optional[str],
        Field(description="Direccion del cliente SOLO si la cita es a domicilio (el cliente la escribio). Omitela si es en el local."),
    ] = None,
    zona: Annotated[
        Optional[str],
        Field(description="Municipio/zona del domicilio (uno de los que devuelve consultar_zonas_domicilio). Solo si es a domicilio."),
    ] = None,
) -> Command:
    """
    Llama esta tool SIEMPRE que vayas a pedirle confirmacion al cliente
    antes de agendar (la primera vez, y de nuevo cada vez que el cliente
    corrija algun dato) - arma el resumen formateado de la cita Y le
    ofrece los botones de seleccion rapida Si/No, en un solo paso. NUNCA
    escribas tu mismo el resumen de confirmacion: esta tool es la UNICA
    forma de mostrarlo, relaya su resultado tal cual en tu respuesta (no
    lo repitas ni lo reformules). No llames crear_cita en este mismo
    turno — eso solo pasa despues de que el cliente confirme, en un
    mensaje aparte.
    """
    if not client_phone:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            "Falta el numero de WhatsApp del cliente y es OBLIGATORIO para agendar (a domicilio o en el local). "
                            "Pidelo y usa registrar_telefono_cliente antes de pedir la confirmacion."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    if not _cliente_dio_fecha(mensajes):
        return Command(update={"messages": [ToolMessage(content=MENSAJE_FALTA_FECHA, tool_call_id=tool_call_id)]})

    servicios = get_employee_services(employee_id) if employee_id else []
    servicio = next((s for s in servicios if s["name"].lower() == servicio_nombre.lower()), None)
    precio_texto = f" · {_formato_precio_cop(servicio['price'])}" if servicio else ""

    empleados_negocio = get_employees(business_id)
    empleado = get_employee_by_id(employee_id) if employee_id else None
    mostrar_empleado = bool(empleado) and len(empleados_negocio) > 1

    error_domicilio = _validar_domicilio(business_id, servicio, direccion, mensajes)
    zona_elegida = None
    if not error_domicilio:
        zona_elegida, error_domicilio = _resolver_zona(business_id, direccion, zona, mensajes)
    if error_domicilio:
        return Command(update={"messages": [ToolMessage(content=error_domicilio, tool_call_id=tool_call_id)]})

    try:
        fecha_hora_dt = datetime.fromisoformat(fecha_hora)
        fecha_texto = formatear_fecha_natural(fecha_hora_dt)
    except ValueError:
        fecha_texto = fecha_hora
        fecha_hora_dt = None

    # Validacion determinista ANTES de pedir confirmacion: asi la unica fuente de
    # "esa hora no esta disponible" es el sistema, no el criterio del modelo.
    if fecha_hora_dt is not None and employee_id:
        duracion_cita = (servicio or {}).get("duration_minutes", 30)
        es_valida, mensaje_error = es_hora_valida(fecha_hora, employee_id, duracion_cita)
        if es_valida:
            hay_choque, mensaje_error = hay_choque_de_horario(business_id, employee_id, fecha_hora_dt, duracion_cita)
            es_valida = not hay_choque
        if not es_valida:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"No se puede agendar esa hora: {mensaje_error} Ofrecele otras horas consultando consultar_horas_disponibles.",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

    lineas = [
        "¡Perfecto! Confírmame estos datos por favor 📋",
        f"💇 Servicio: *{servicio_nombre}*{precio_texto}",
    ]
    if mostrar_empleado:
        lineas.append(f"🧑 Con: *{empleado.get('name') or 'el equipo'}*")
    lineas.append(f"📅 Cuándo: *{fecha_texto}*")
    if direccion and direccion.strip():
        lineas.append(f"🏠 Domicilio en: *{direccion.strip()}*")
        if zona_elegida and float(zona_elegida["fee"]) > 0:
            recargo = float(zona_elegida["fee"])
            total = float(servicio["price"]) + recargo if servicio else recargo
            lineas.append(f"🚚 Recargo por domicilio ({zona_elegida['name']}): *+{_formato_precio_cop(recargo)}*")
            lineas.append(f"💰 Total: *{_formato_precio_cop(total)}*")
    lineas.append(f"👤 Nombre: *{nombre_cliente}*")
    if client_phone:
        lineas.append(f"📱 Número: *{_formato_telefono_visible(client_phone)}*")
    lineas.append("¿Confirmo tu cita?")

    return Command(
        update={
            "ultimas_opciones": [
                {"label": "Si, confirmar", "value": "Si"},
                {"label": "No, cambiar algo", "value": "No"},
            ],
            "messages": [
                ToolMessage(content="\n".join(lineas), name="pedir_confirmacion_cita", tool_call_id=tool_call_id)
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
    employee_fijo: EmployeeFijo,
    session_id: SessionId,
    mensajes: Annotated[list, InjectedState("messages")],
    direccion: Annotated[
        Optional[str],
        Field(description="Direccion del cliente SOLO si la cita es a domicilio (la misma que se confirmo). Omitela si es en el local."),
    ] = None,
    zona: Annotated[
        Optional[str],
        Field(description="Municipio/zona del domicilio (uno de los que devuelve consultar_zonas_domicilio). Solo si es a domicilio."),
    ] = None,
) -> dict:
    """
    Agenda la cita para el cliente, validando horario y disponibilidad del
    empleado seleccionado. Si el servicio requiere abono
    (payment_type/requires_payment del servicio), la cita AUN NO se crea:
    se genera un link de pago de Wompi y la cita se agenda automaticamente
    (sin que el negocio tenga que hacer nada) solo cuando Wompi confirme
    el pago - el resultado trae pago_pendiente=True en vez de cita_creada
    en ese caso. Revisa el campo pago_pendiente en el resultado para saber
    cual de los dos casos ocurrio.
    """
    if not client_phone:
        return {"error": "Antes de agendar necesito el numero de WhatsApp del cliente. Pidelo y usa registrar_telefono_cliente."}
    if not employee_id:
        return {"error": "Antes de agendar necesito saber con que empleado es la cita. Usa consultar_empleados_disponibles y seleccionar_empleado."}

    servicios = get_employee_services(employee_id)
    servicio = next((s for s in servicios if s["name"].lower() == servicio_nombre.lower()), None)
    if not servicio:
        return {"error": f"No encontre el servicio '{servicio_nombre}' para ese empleado. Servicios disponibles: {[s['name'] for s in servicios]}"}

    duracion = servicio.get("duration_minutes", 30)

    error_domicilio = _validar_domicilio(business_id, servicio, direccion, mensajes)
    zona_elegida = None
    if not error_domicilio:
        zona_elegida, error_domicilio = _resolver_zona(business_id, direccion, zona, mensajes)
    if error_domicilio:
        return {"error": error_domicilio}

    es_valida, mensaje_error = es_hora_valida(fecha_hora, employee_id, duracion)
    if not es_valida:
        return {"error": mensaje_error}

    fecha_hora_dt = datetime.fromisoformat(fecha_hora)

    hay_choque, mensaje_choque = hay_choque_de_horario(business_id, employee_id, fecha_hora_dt, duracion)
    if hay_choque:
        return {"error": mensaje_choque}

    direccion_limpia = direccion.strip() if direccion and direccion.strip() else None
    zona_nombre = zona_elegida["name"] if zona_elegida else None
    zona_fee = float(zona_elegida["fee"]) if zona_elegida else 0

    if servicio.get("requires_payment"):
        # Se agenda de inmediato como "pending_payment": bloquea el cupo
        # desde ya (cuenta para el choque de horario y para el indice
        # unico de appointments_pending_payment.sql), aunque todavia no es
        # una cita confirmada. Pasa a "confirmed" solo cuando Wompi
        # apruebe el pago (ver confirmar_cita_desde_pago en
        # services/wompi_payment_requests.py); si el link vence sin pagar,
        # se cancela y libera el cupo (expirar_solicitudes_vencidas).
        from services.appointment_confirmation import crear_cita_pendiente_pago
        from services.wompi_payment_requests import crear_solicitud_pago

        cita_pendiente = crear_cita_pendiente_pago(
            business_id=business_id,
            client_phone=client_phone,
            client_name=nombre_cliente,
            service_id=servicio["id"],
            scheduled_at=fecha_hora,
            employee_id=employee_id,
            address=direccion_limpia,
            home_visit_zone=zona_nombre,
            home_visit_fee=zona_fee,
        )

        if not cita_pendiente:
            # El indice unico rechazo el insert: alguien mas tomo ese
            # horario en el mismo instante (carrera real de concurrencia).
            return {
                "error": "Esa hora se acaba de ocupar (otro cliente la tomo justo ahora). Ofrecele consultar_horas_disponibles de nuevo para elegir otra."
            }

        try:
            solicitud = crear_solicitud_pago(
                business_id=business_id,
                servicio=servicio,
                session_id=session_id,
                client_phone=client_phone,
                appointment_id=cita_pendiente["id"],
                # Solo si el chat era el enlace propio de un empleado: el
                # localStorage del widget guarda el session_id bajo una
                # clave con employee_id SOLO en ese caso (ver
                # chat.page.ts), asi que enviar el employee_id cuando el
                # cliente esta en el chat general haria que el redirect lo
                # mande a una URL donde el widget no encuentra su sesion.
                employee_id=employee_id if employee_fijo else None,
                expira_en_horas=1.0,
            )
        except Exception as e:
            print(f"No se pudo generar el link de pago de Wompi para la cita {cita_pendiente['id']}: {e}")
            solicitud = None

        if not solicitud:
            # No se pudo generar el link: no dejar el cupo bloqueado sin
            # forma de pagarlo, se libera de inmediato.
            from services.db import update_appointment_status

            update_appointment_status(cita_pendiente["id"], "cancelled")
            return {
                "error": (
                    "Este servicio requiere un abono para agendar, pero no fue posible generar el link de pago "
                    "en este momento (problema tecnico o el negocio no tiene Wompi configurado). Explicaselo al "
                    "cliente y ofrece transferir_a_equipo si insiste en agendar ahora."
                )
            }

        return {
            "pago_pendiente": True,
            "link_pago": solicitud["checkout_url"],
            "monto_abono_cents": solicitud["amount_in_cents"],
            "descripcion_abono": solicitud["description"],
        }

    from services.appointment_confirmation import finalizar_creacion_cita

    cita = finalizar_creacion_cita(
        business_id=business_id,
        client_phone=client_phone,
        client_name=nombre_cliente,
        service_id=servicio["id"],
        service_name=servicio["name"],
        scheduled_at=fecha_hora,
        employee_id=employee_id,
        address=direccion_limpia,
        home_visit_zone=zona_nombre,
        home_visit_fee=zona_fee,
    )

    return {"cita_creada": [cita] if cita else []}


@tool
def consultar_citas_cliente(business_id: BusinessId, client_phone: ClientPhone) -> dict:
    """Consulta las citas confirmadas del cliente actual."""
    if not client_phone:
        return {"error": "Necesito el numero de WhatsApp del cliente para buscar sus citas. Pidelo y usa registrar_telefono_cliente."}
    return {"citas": get_client_appointments(business_id, client_phone)}


@tool
def cancelar_cita(
    appointment_id: Annotated[str, Field(description="ID de la cita a cancelar.")],
    business_id: BusinessId,
    client_phone: ClientPhone,
) -> dict:
    """Cancela una cita existente del cliente."""
    if not client_phone:
        return {"error": "Necesito el numero de WhatsApp del cliente para poder cancelar. Pidelo y usa registrar_telefono_cliente."}

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
        return {"error": "Necesito el numero de WhatsApp del cliente para reprogramar. Pidelo y usa registrar_telefono_cliente."}

    cita_actual = get_appointment_by_id_and_phone(appointment_id, client_phone)
    if not cita_actual:
        return {"error": "No encontre esa cita para este cliente."}

    empleado_id_cita = cita_actual["employee_id"]
    fecha_hora_anterior = datetime.fromisoformat(cita_actual["scheduled_at"])
    duracion = cita_actual.get("services", {}).get("duration_minutes", 30) if cita_actual.get("services") else 30
    nombre_servicio = cita_actual.get("services", {}).get("name", "tu cita") if cita_actual.get("services") else "tu cita"

    es_valida, mensaje_error = es_hora_valida(nueva_fecha_hora, empleado_id_cita, duracion)
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
def transferir_a_equipo(
    business_id: BusinessId,
    session_id: SessionId,
    client_phone: ClientPhone,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Usa esta tool INMEDIATAMENTE cuando el cliente pida explicitamente
    hablar con una persona real, con el equipo, con el dueño del negocio, o
    con alguien por su nombre propio, en cualquier momento de la
    conversacion (no solo al inicio). Esto detiene al bot para que un
    humano tome la conversacion.
    """
    _notificar_negocio_escalamiento(business_id, session_id, client_phone or "Un cliente")

    return Command(
        update={
            "transferido": True,
            "messages": [
                ToolMessage(content="Cliente transferido a un humano del equipo.", tool_call_id=tool_call_id)
            ],
        }
    )


@tool
def escalar_por_confusion(
    business_id: BusinessId,
    session_id: SessionId,
    client_phone: ClientPhone,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Usa esta tool cuando, despues de intentar aclarar la solicitud del
    cliente al menos una vez con una pregunta directa, sigues sin entender
    que necesita, o cuando ninguna de tus otras herramientas te permite
    ayudarlo con lo que pide. Avisa al negocio para que un humano siga la
    conversacion (el cliente NUNCA se entera de este aviso: no le
    menciones que vas a notificar a nadie ni le ofrezcas hablar con una
    persona). NO la uses en la primera pregunta ambigua (primero intenta
    aclarar tu mismo), ni la repitas si el estado de la conversacion
    indica que ya se notifico antes, a menos que el cliente pida
    explicitamente hablar con alguien (en ese caso usa transferir_a_equipo).
    """
    _notificar_negocio_escalamiento(business_id, session_id, client_phone or "Un cliente")

    mensaje = "Dejame confirmar eso con el equipo, en un momento seguimos por aqui mismo 🙏"

    return Command(
        update={
            "escalado": True,
            "messages": [ToolMessage(content=mensaje, tool_call_id=tool_call_id)],
        }
    )


@tool
def consultar_zonas_domicilio(
    business_id: BusinessId,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """
    Consulta en que municipios/zonas el negocio hace domicilios y el recargo
    de cada una. Usala cuando el cliente pregunte donde llegan o cuanto
    cuesta el domicilio, o antes de pedirle la direccion. Si la lista esta
    vacia, el negocio no restringe zonas ni cobra recargo.
    """
    if not _domicilios_activos(business_id):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Este negocio no hace domicilios: todas las citas son en el local.",
                        name="consultar_zonas_domicilio",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    zonas = get_home_visit_zones(business_id)
    resultado = {
        "zonas": [{"nombre": z["name"], "recargo": _formato_precio_cop(z["fee"])} for z in zonas],
        "sin_restriccion": not zonas,
    }
    return Command(
        update={
            "messages": [ToolMessage(content=str(resultado), name="consultar_zonas_domicilio", tool_call_id=tool_call_id)]
        }
    )


TOOLS = [
    registrar_telefono_cliente,
    consultar_empleados_disponibles,
    seleccionar_empleado,
    consultar_servicios_disponibles,
    seleccionar_servicio,
    consultar_horas_disponibles,
    consultar_zonas_domicilio,
    pedir_confirmacion_cita,
    crear_cita,
    consultar_citas_cliente,
    cancelar_cita,
    reprogramar_cita,
    transferir_a_equipo,
    escalar_por_confusion,
]
