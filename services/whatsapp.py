import os
import requests
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")  # mismo token (System User) sirve para todos los numeros de esta app de Meta


def send_whatsapp_message(to: str, text: str, business_phone_number_id: str):
    """
    Envia un mensaje de WhatsApp usando la API oficial de Meta (Cloud API),
    desde el numero especifico del negocio (business_phone_number_id).
    Esto permite que cada negocio responda desde su propio numero de WhatsApp,
    aunque todos compartan el mismo token de System User.
    """
    graph_api_url = f"https://graph.facebook.com/v23.0/{business_phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
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