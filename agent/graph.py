from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from agent.prompts import build_system_prompt
from agent.state import AgentState
from agent.tools import TOOLS
from core.settings import CHAT_MODEL_NAME, DATABASE_URL, OPENAI_API_KEY
from services.ai_usage_tracking import registrar_uso
from services.db import get_business_by_id, supabase as client_supabase
from services.scheduling import DIAS_SEMANA_ES, ahora_local

MENSAJE_TRANSFERIDO = (
    "¡Perfecto! 🙌 En un momento una persona de nuestro equipo te atiende.\n\n"
    "Gracias por tu paciencia 😊"
)
MENSAJE_ERROR_TECNICO = (
    "Disculpa 🙏 estamos teniendo un problema técnico momentáneo.\n\n"
    "Por favor inténtalo de nuevo en un par de minutos 😊"
)
MENSAJE_LIMITE_RONDAS = "¡Ya casi terminamos! 😊 ¿Me confirmas de nuevo qué necesitas para continuar?"
MENSAJE_NEGOCIO_NO_DISPONIBLE = (
    "Este negocio todavia no tiene el agendamiento automatico activado. "
    "Contactalos directamente para agendar tu cita."
)

RECURSION_LIMIT = 12

# Pool y checkpointer se crean una sola vez al importar el modulo (mismo
# patron que el singleton `supabase` de services/db.py). setup() crea las
# tablas propias del checkpointer (checkpoints, checkpoint_writes, etc) la
# primera vez que corre; en corridas siguientes es un no-op seguro.
_pool = ConnectionPool(
    conninfo=DATABASE_URL,
    kwargs={"autocommit": True, "row_factory": dict_row},
    open=True,
    max_size=10,
)
_checkpointer = PostgresSaver(_pool)
_checkpointer.setup()

_model = ChatOpenAI(model=CHAT_MODEL_NAME, api_key=OPENAI_API_KEY, max_retries=3).bind_tools(TOOLS)


def _construir_horario_texto(business_id: str) -> str:
    horario_response = (
        client_supabase.table("business_hours").select("*").eq("business_id", business_id).execute()
    )
    dias_map = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    horario_por_dia = {row["day"]: row for row in horario_response.data}

    lineas = []
    for dia in dias_map:
        info = horario_por_dia.get(dia)
        if not info or not info.get("is_open"):
            lineas.append(f"{DIAS_SEMANA_ES[dia]}: cerrado")
        else:
            linea = f"{DIAS_SEMANA_ES[dia]}: {info['opening_time'][:5]} a {info['closing_time'][:5]}"
            if info.get("lunch_start") and info.get("lunch_end"):
                linea += f" (almuerzo {info['lunch_start'][:5]} a {info['lunch_end'][:5]}, no se agenda en ese rango)"
            lineas.append(linea)
    return "; ".join(lineas)


def _historial_valido_para_modelo(messages: list) -> list:
    """
    Reconstruye el historial en el orden que exige la API de OpenAI: cada
    AIMessage con tool_calls debe ir seguido inmediatamente de un
    ToolMessage por cada tool_call_id. Si un tool step se cae a mitad de
    camino (crash del proceso, excepcion no controlada, etc.) el
    checkpoint puede quedar con tool_calls sin respuesta, o con la
    respuesta agregada al final en vez de justo despues. Sin este
    arreglo, la sesion queda invalida para siempre: OpenAI rechaza el
    mismo historial en cada turno futuro y el bot repite el mensaje de
    error tecnico sin parar.
    """
    tool_messages_por_id = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    resultado = []
    for mensaje in messages:
        if isinstance(mensaje, ToolMessage):
            continue  # se reinsertan justo despues de su AIMessage, abajo
        resultado.append(mensaje)
        if isinstance(mensaje, AIMessage) and mensaje.tool_calls:
            for tool_call in mensaje.tool_calls:
                tool_msg = tool_messages_por_id.get(tool_call["id"])
                if tool_msg is None:
                    tool_msg = ToolMessage(
                        content="Hubo un problema tecnico ejecutando esta accion, ignorala.",
                        tool_call_id=tool_call["id"],
                    )
                resultado.append(tool_msg)
    return resultado


def _agent_node(state: AgentState) -> dict:
    if state.get("transferido"):
        return {"messages": [AIMessage(content=MENSAJE_TRANSFERIDO)]}

    business = get_business_by_id(state["business_id"])
    if not business:
        return {"messages": [AIMessage(content=MENSAJE_ERROR_TECNICO)]}

    horario_texto = _construir_horario_texto(state["business_id"])
    fecha_actual = ahora_local().strftime("%Y-%m-%d %H:%M (%A)")
    system = build_system_prompt(business, horario_texto, fecha_actual, state.get("client_phone"))

    historial = _historial_valido_para_modelo(state["messages"])
    try:
        response = _model.invoke([SystemMessage(content=system)] + historial)
    except Exception as e:
        print(f"Error invocando al modelo de IA: {e}")
        return {"messages": [AIMessage(content=MENSAJE_ERROR_TECNICO)]}

    usage = getattr(response, "usage_metadata", None) or {}
    if usage:
        registrar_uso(
            state["business_id"],
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("total_tokens", 0),
        )

    return {"messages": [response]}


def _build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("agent", _agent_node)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile(checkpointer=_checkpointer)


GRAPH = _build_graph()


def _thread_config(business_id: str, session_id: str) -> dict:
    # El business_id como prefijo del thread_id es lo que aisla las
    # conversaciones por negocio: aunque dos negocios usen el mismo
    # session_id, sus hilos quedan en claves distintas en el checkpointer.
    return {
        "configurable": {"thread_id": f"{business_id}:{session_id}"},
        "recursion_limit": RECURSION_LIMIT,
    }


def enviar_mensaje(business_id: str, session_id: str, mensaje: str) -> str:
    """Manda un mensaje del cliente al agente y devuelve la respuesta en texto."""
    config = _thread_config(business_id, session_id)
    try:
        resultado = GRAPH.invoke(
            {"messages": [HumanMessage(content=mensaje)], "business_id": business_id},
            config=config,
        )
    except GraphRecursionError:
        return MENSAJE_LIMITE_RONDAS

    for mensaje_respuesta in reversed(resultado["messages"]):
        if isinstance(mensaje_respuesta, AIMessage) and mensaje_respuesta.content:
            return mensaje_respuesta.content
    return MENSAJE_ERROR_TECNICO


def obtener_historial(business_id: str, session_id: str) -> list[dict]:
    """Lee el historial persistido de una sesion (solo turnos humano/IA, sin tool calls)."""
    config = _thread_config(business_id, session_id)
    snapshot = GRAPH.get_state(config)
    if not snapshot or not snapshot.values:
        return []

    historial = []
    for mensaje in snapshot.values.get("messages", []):
        if isinstance(mensaje, HumanMessage) and mensaje.content:
            historial.append({"role": "user", "content": mensaje.content})
        elif isinstance(mensaje, AIMessage) and mensaje.content:
            historial.append({"role": "assistant", "content": mensaje.content})
    return historial
