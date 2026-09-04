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
    employee_id identifica con que empleado es la cita: en el chat de un
    empleado especifico lo manda la ruta en cada turno (igual que
    business_id, no lo decide el LLM); en el chat general del negocio lo
    llena la tool seleccionar_empleado una vez el cliente elige.
    employee_fijo indica si employee_id vino fijado por la URL (chat de un
    empleado especifico): en ese caso el agente nunca debe preguntar por
    otro empleado ni llamar seleccionar_empleado.
    service_id: se llena cuando la tool seleccionar_servicio corre, una
    vez el cliente deja claro con que servicio quiere la cita. Sirve para
    que _extraer_opciones (graph.py) deje de mostrar los botones de "elegir
    servicio" en turnos posteriores, aunque el bot vuelva a llamar
    consultar_servicios_disponibles para confirmar precio/duracion (esto
    evita el bug de re-ofrecer la lista completa cuando en realidad el
    bot ya iba a pedir la fecha).
    ultimas_opciones: opciones de seleccion rapida (botones) para el widget
    de chat, dejadas por la ultima tool de listado (empleados, servicios u
    horas) que corrio en el turno actual. graph.py solo las usa si una de
    esas tools corrio en el turno actual (ver _extraer_opciones en
    graph.py): no hay que preocuparse de "limpiarlas" aqui porque un turno
    que no vuelve a llamar ninguna de esas tools simplemente no las expone,
    aunque el valor persistido quede desactualizado en el checkpointer.
    escalado: se pone en True cuando la tool escalar_por_confusion corre
    (el bot no entendio al cliente pese a intentar aclarar, o no pudo
    ayudarlo con las tools disponibles) y notifica al negocio. A
    diferencia de transferido, NO congela la conversacion: solo se usa
    para que el prompt sepa que ya se notifico y no lo repita sin
    necesidad.
    session_id identifica la conversacion (junto con business_id forma el
    thread_id del checkpointer, ver agent/graph.py:_thread_config). Se
    manda en cada invocacion desde la ruta igual que business_id, nunca lo
    decide el LLM. Las tools de escalamiento lo usan para armar el link a
    la conversacion que se le manda al negocio en la notificacion.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    business_id: str
    session_id: str
    client_phone: Annotated[Optional[str], _ultimo_valor]
    employee_id: Annotated[Optional[str], _ultimo_valor]
    employee_fijo: Annotated[bool, _ultimo_valor]
    service_id: Annotated[Optional[str], _ultimo_valor]
    transferido: Annotated[bool, _ultimo_valor]
    ultimas_opciones: Annotated[Optional[list[dict]], _ultimo_valor]
    escalado: Annotated[bool, _ultimo_valor]
