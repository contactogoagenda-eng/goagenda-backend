from typing import Annotated, Optional

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


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
    client_phone: Optional[str]
    transferido: bool
