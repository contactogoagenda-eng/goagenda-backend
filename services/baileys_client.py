import os

import requests
from dotenv import load_dotenv

load_dotenv()

# URL publica del servicio de Baileys y la API key interna que este exige
# (header x-api-key). Deben coincidir con las variables del baileys-service.
BAILEYS_SERVICE_URL = os.getenv("BAILEYS_SERVICE_URL", "http://localhost:3000")
BAILEYS_INTERNAL_API_KEY = os.getenv("BAILEYS_INTERNAL_API_KEY", "")

_HEADERS = {"x-api-key": BAILEYS_INTERNAL_API_KEY}


def send_baileys_message(business_id: str, to: str, text: str):
    """
    Envia un mensaje de WhatsApp a traves del baileys-service, desde el
    numero vinculado del negocio. Devuelve el JSON del servicio, o un dict
    con "error" si el envio fallo (mismo contrato que send_whatsapp_message
    de Meta, para poder usarlos de forma intercambiable).
    """
    try:
        response = requests.post(
            f"{BAILEYS_SERVICE_URL}/send-message",
            json={"business_id": business_id, "to": to, "text": text},
            headers=_HEADERS,
            timeout=20,
        )
        data = response.json()
        if response.status_code != 200:
            return {"error": data.get("error", f"HTTP {response.status_code}")}
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error de conexion con el baileys-service: {e}")
        return {"error": str(e)}


def solicitar_pairing_code(business_id: str, phone: str):
    """Pide un codigo de vinculacion al baileys-service."""
    response = requests.post(
        f"{BAILEYS_SERVICE_URL}/pairing-code/{business_id}",
        json={"phone": phone},
        headers=_HEADERS,
        timeout=30,
    )
    return response.json()


def estado_conexion(business_id: str):
    """Consulta el estado de la conexion de WhatsApp de un negocio."""
    response = requests.get(
        f"{BAILEYS_SERVICE_URL}/status/{business_id}",
        headers=_HEADERS,
        timeout=15,
    )
    return response.json()
