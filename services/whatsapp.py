import os
import requests
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")  # mismo token (System User) sirve para todos los numeros de esta app de Meta

# Numero de WhatsApp Business propio de GoAgenda (no de un negocio individual),
# usado para notificar directamente a los CLIENTES finales (ej. confirmacion
# de cita) sin depender de que cada negocio haya conectado su propio numero.
# Token separado porque puede vivir en una app/WABA de Meta distinta a la de
# los numeros por-negocio.
GOAGENDA_WHATSAPP_TOKEN = os.getenv("GOAGENDA_WHATSAPP_TOKEN")
GOAGENDA_WHATSAPP_PHONE_NUMBER_ID = os.getenv("GOAGENDA_WHATSAPP_PHONE_NUMBER_ID")

# Message template aprobado en Meta Business Manager para la confirmacion de
# cita (categoria UTILITY). Necesario porque el cliente agenda por el chat
# web y nunca le ha escrito a este numero: un mensaje de texto libre SIEMPRE
# lo rechaza Meta fuera de la ventana de 24h (error 131047, "re-engagement
# message"). El texto exacto (con las 4 variables en este orden: nombre del
# cliente, nombre del negocio, servicio, fecha/hora) esta documentado en el
# README, seccion WhatsApp - si se edita la copia hay que volver a crear/
# aprobar el template en Meta con el mismo texto antes de cambiarlo aqui.
GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE = os.getenv("GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE", "confirmacion_cita_goagenda")
GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE_LANG = os.getenv("GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE_LANG", "es_CO")

# Message template aprobado en Meta Business Manager para el recordatorio de
# cita (categoria UTILITY). Se usa en vez de texto libre cuando el cliente
# nunca le ha escrito al numero propio del negocio, o ya paso mas de 24h
# desde su ultimo mensaje (services/db.py:cliente_dentro_de_ventana_24h) -
# un mensaje de texto libre en ese caso lo rechaza Meta (error 131047).
# El texto exacto (con las 4 variables en este orden: nombre del cliente,
# nombre del negocio, servicio, fecha/hora) debe crearse y aprobarse en
# Meta con el mismo nombre configurado aqui antes de que esto funcione.
WHATSAPP_REMINDER_TEMPLATE = os.getenv("WHATSAPP_REMINDER_TEMPLATE", "recordatorio_cita_goagenda")
WHATSAPP_REMINDER_TEMPLATE_LANG = os.getenv("WHATSAPP_REMINDER_TEMPLATE_LANG", "es_CO")


def construir_mensaje_contacto_humano(business: dict | None) -> str:
    """
    Arma el mensaje que el agente de chat le comparte al cliente cuando
    necesita "rendirse" y pasarlo a un humano: no entendio su solicitud
    pese a intentar aclarar, o hubo un fallo tecnico (ver
    agent/tools.py:escalar_por_confusion y agent/graph.py). Si el telefono
    guardado en businesses.phone_number normaliza a un WhatsApp colombiano
    valido, arma un enlace wa.me clickeable; si no hay telefono o no
    normaliza, cae a un mensaje generico sin numero.
    """
    numero = business.get("phone_number") if business else None
    numero_normalizado = normalizar_numero_whatsapp(numero) if numero else None

    if numero_normalizado:
        return f"📱 Escríbele directo al negocio por WhatsApp: https://wa.me/{numero_normalizado}"
    return "📱 Contacta directamente al negocio, en un momento te ayudan."


def normalizar_numero_whatsapp(numero: str) -> str | None:
    """
    Valida y normaliza un numero de WhatsApp a formato internacional sin
    "+" (el que espera la Cloud API de Meta como "to" al enviar mensajes),
    asumiendo Colombia cuando el cliente lo da sin indicativo. Devuelve
    None si no parece un celular colombiano valido: 10 digitos empezando
    en 3 (con o sin el indicativo 57 adelante). No acepta telefonos fijos
    (no reciben WhatsApp). La usan tanto el agente de chat
    (agent/tools.py:registrar_telefono_cliente) como la creacion manual de
    citas desde el panel (routes/manual_appointments.py).
    """
    limpio = "".join(c for c in numero if c.isdigit())

    if limpio.startswith("57") and len(limpio) == 12:
        limpio = limpio[2:]

    if len(limpio) == 10 and limpio[0] == "3":
        return f"57{limpio}"

    return None


def _enviar_mensaje_texto(to: str, text: str, phone_number_id: str, token: str):
    """
    POST generico a la Cloud API de Meta (`/{phone_number_id}/messages`) con
    un mensaje de texto libre. Nota: Meta solo permite texto libre dentro de
    la ventana de 24h desde el ultimo mensaje del cliente a ese numero; para
    contactar primero a un numero que nunca escribio (como los clientes que
    reservan por el chat web y jamas le escribieron a este numero) hace
    falta un message template pre-aprobado. Ver README, seccion WhatsApp.
    """
    graph_api_url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    try:
        response = requests.post(graph_api_url, headers=headers, json=payload, timeout=15)
        print(f"Meta API status: {response.status_code}")
        print(f"Meta API response: {response.text}")

        if response.status_code != 200:
            return {"error": response.json()}

        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error de conexion con la API de Meta: {e}")
        return {"error": str(e)}


def _enviar_mensaje_template(to: str, phone_number_id: str, token: str, template_name: str, language_code: str, parametros_body: list[str]):
    """
    POST a la Cloud API de Meta con un mensaje de tipo "template". A
    diferencia del texto libre, un template aprobado por Meta SI puede
    iniciar una conversacion con un numero que nunca le ha escrito a este
    numero de WhatsApp - lo que aplica siempre a los clientes que agendan
    por el chat web.
    """
    graph_api_url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": valor} for valor in parametros_body],
                }
            ],
        },
    }

    try:
        response = requests.post(graph_api_url, headers=headers, json=payload, timeout=15)
        print(f"Meta API (template) status: {response.status_code}")
        print(f"Meta API (template) response: {response.text}")

        if response.status_code != 200:
            return {"error": response.json()}

        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error de conexion con la API de Meta: {e}")
        return {"error": str(e)}


def send_whatsapp_message(to: str, text: str, business_phone_number_id: str):
    """
    Envia un mensaje de WhatsApp usando la API oficial de Meta (Cloud API),
    desde el numero especifico del negocio (business_phone_number_id).
    Esto permite que cada negocio responda desde su propio numero de WhatsApp,
    aunque todos compartan el mismo token de System User.
    """
    return _enviar_mensaje_texto(to, text, business_phone_number_id, WHATSAPP_TOKEN)


def enviar_confirmacion_cita_cliente(client_phone: str, nombre_cliente: str, nombre_negocio: str, nombre_servicio: str, fecha_hora_texto: str):
    """
    Le confirma al cliente, por WhatsApp, que su cita quedo agendada. Se
    manda como message template (GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE),
    no como texto libre: el cliente agenda por el chat web y nunca le ha
    escrito a este numero, asi que un mensaje de texto libre lo rechaza
    Meta siempre por estar fuera de la ventana de 24h.
    """
    if not GOAGENDA_WHATSAPP_TOKEN or not GOAGENDA_WHATSAPP_PHONE_NUMBER_ID:
        print("Faltan GOAGENDA_WHATSAPP_TOKEN/GOAGENDA_WHATSAPP_PHONE_NUMBER_ID, no se envia la confirmacion al cliente.")
        return {"error": "credenciales de WhatsApp de GoAgenda no configuradas"}

    return _enviar_mensaje_template(
        to=client_phone,
        phone_number_id=GOAGENDA_WHATSAPP_PHONE_NUMBER_ID,
        token=GOAGENDA_WHATSAPP_TOKEN,
        template_name=GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE,
        language_code=GOAGENDA_WHATSAPP_CONFIRMATION_TEMPLATE_LANG,
        parametros_body=[nombre_cliente, nombre_negocio, nombre_servicio, fecha_hora_texto],
    )


def enviar_recordatorio_cita_template(
    to: str, nombre_cliente: str, nombre_negocio: str, nombre_servicio: str, fecha_hora_texto: str, business_phone_number_id: str
):
    """
    Envia el recordatorio de cita como message template (WHATSAPP_REMINDER_TEMPLATE),
    para cuando el cliente nunca le ha escrito al numero del negocio o ya
    paso mas de 24h desde su ultimo mensaje. A diferencia de la
    confirmacion (que sale del numero propio de GoAgenda), el recordatorio
    sale del numero propio del negocio (business_phone_number_id), igual
    que el envio de texto libre en services/reminder_service.py.
    """
    return _enviar_mensaje_template(
        to=to,
        phone_number_id=business_phone_number_id,
        token=WHATSAPP_TOKEN,
        template_name=WHATSAPP_REMINDER_TEMPLATE,
        language_code=WHATSAPP_REMINDER_TEMPLATE_LANG,
        parametros_body=[nombre_cliente, nombre_negocio, nombre_servicio, fecha_hora_texto],
    )