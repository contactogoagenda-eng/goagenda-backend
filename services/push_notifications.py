import firebase_admin
from firebase_admin import credentials, messaging
import os
import json

_firebase_app = None

# TODO: pega aqui la URL publica real de tu logo subido a Supabase Storage
LOGO_URL = "https://ghwkiuwcovrmpztamjgz.supabase.co/storage/v1/object/public/assets/icono_goagenda.png"


def _inicializar_firebase():
    """
    Inicializa Firebase Admin SDK una sola vez (singleton).
    Soporta 2 formas de dar las credenciales:
    1. Archivo local firebase-credentials.json (desarrollo en tu PC)
    2. Variable de entorno FIREBASE_CREDENTIALS_JSON con el contenido
       completo del JSON pegado como texto (produccion en Railway, donde
       no es practico subir un archivo)
    """
    global _firebase_app
    if _firebase_app is None:
        credenciales_env = os.getenv("FIREBASE_CREDENTIALS_JSON")
        if credenciales_env:
            cred_dict = json.loads(credenciales_env)
            cred = credentials.Certificate(cred_dict)
        else:
            ruta_credenciales = os.path.join(os.path.dirname(__file__), "..", "firebase-credentials.json")
            cred = credentials.Certificate(ruta_credenciales)
        _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def _enviar_push(fcm_token: str, titulo: str, cuerpo: str):
    """
    Funcion generica que envia una notificacion push al dispositivo del negocio.
    Si fcm_token es None o vacio, no hace nada (el negocio no ha registrado su dispositivo aun).
    """
    if not fcm_token:
        print("No hay fcm_token registrado para este negocio, no se envia notificacion.")
        return

    try:
        _inicializar_firebase()

        mensaje = messaging.Message(
            notification=messaging.Notification(title=titulo, body=cuerpo),
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="citas_nuevas_v5",
                    priority="high",
                    default_sound=True,
                    icon="ic_stat_name",
                    color="#03A580",
                    image=LOGO_URL,
                ),
            ),
            token=fcm_token,
        )

        respuesta = messaging.send(mensaje)
        print(f"Notificacion push enviada correctamente: {respuesta}")
    except Exception as e:
        print(f"Error enviando notificacion push: {e}")


def enviar_notificacion_nueva_cita(fcm_token: str, nombre_cliente: str, servicio: str, fecha_hora_texto: str):
    """Notifica al negocio cuando se crea una cita nueva (por WhatsApp o manual)."""
    _enviar_push(
        fcm_token,
        titulo="Nueva cita agendada",
        cuerpo=f"{nombre_cliente} agendó {servicio} para {fecha_hora_texto}",
    )


def enviar_notificacion_cita_cancelada(fcm_token: str, nombre_cliente: str, servicio: str, fecha_hora_texto: str):
    """Notifica al negocio cuando un cliente cancela una cita por WhatsApp."""
    _enviar_push(
        fcm_token,
        titulo="Cita cancelada",
        cuerpo=f"{nombre_cliente} canceló su cita de {servicio} del {fecha_hora_texto}",
    )


def enviar_notificacion_cita_reprogramada(
    fcm_token: str, nombre_cliente: str, servicio: str, fecha_anterior_texto: str, fecha_nueva_texto: str
):
    """Notifica al negocio cuando un cliente reprograma (mueve) una cita por WhatsApp."""
    _enviar_push(
        fcm_token,
        titulo="Cita reprogramada",
        cuerpo=f"{nombre_cliente} movió su cita de {servicio} del {fecha_anterior_texto} al {fecha_nueva_texto}",
    )