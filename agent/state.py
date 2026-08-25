from typing import Annotated, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def _ultimo_valor(_anterior, nuevo):
    """
    Reducer 'ultimo gana': el LLM a veces pide dos tool calls en el mismo
    turno (ej. registrar_telefono_cliente llamada dos veces). Sin un
    Annotated con reducer, LangGraph trata estas claves como LastValue y
    lanza InvalidUpdateError si recibe mas de una escritura en el mismo
    super-step. Con este reducer, varias escrituras en el mismo turno se
    resuelven quedandose con la ultima.
    """
    return nuevo


class AgentState(TypedDict):
    """
    Estado persistido por el checkpointer (uno por thread_id, es decir por
    sesion de chat de un negocio). business_id se manda en cada invocacion
    del grafo desde la ruta (nunca lo decide el LLM), asi que las tools lo
    inyectan desde aqui para que un negocio jamas pueda tocar datos de otro.
    client_phone se llena solo cuando el cliente lo da en la conversacion
    (tool registrar_telefono_cliente); mientras no exista, las tools que
    necesitan identificar al cliente deben pedirlo primero.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    business_id: str
    client_phone: Annotated[Optional[str], _ultimo_valor]
    transferido: Annotated[bool, _ultimo_valor]
