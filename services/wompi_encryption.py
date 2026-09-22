"""
Encriptacion de las llaves de Wompi antes de guardarlas en la base de datos.

Usa Fernet (AES-128-CBC + HMAC-SHA256 autenticado, de la libreria
`cryptography`) con una unica llave maestra leida de la variable de entorno
WOMPI_ENCRYPTION_KEY - esa llave NUNCA se guarda en la base de datos, solo
vive en el entorno del backend. Las llaves de Wompi en si (publica, privada,
de eventos) son por-negocio y se guardan encriptadas en wompi_credentials
(ver services/wompi_credentials.py); nunca en variables de entorno, porque
cada negocio tiene las suyas (multi-tenant).

Generar la llave maestra una sola vez:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
y pegarla en WOMPI_ENCRYPTION_KEY. Si esta llave se pierde, las credenciales
de Wompi ya guardadas quedan irrecuperables (hay que volver a pedirselas a
cada negocio) - por eso debe respaldarse igual que cualquier otro secreto de
produccion.
"""

import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

WOMPI_ENCRYPTION_KEY = os.getenv("WOMPI_ENCRYPTION_KEY")

if not WOMPI_ENCRYPTION_KEY:
    raise RuntimeError(
        "Falta WOMPI_ENCRYPTION_KEY: la llave maestra para encriptar las credenciales de Wompi "
        "de cada negocio. Generala con:\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
        "y agregala a tu .env. Nunca la compartas ni la subas a git."
    )

try:
    _fernet = Fernet(WOMPI_ENCRYPTION_KEY.encode())
except (ValueError, TypeError) as e:
    raise RuntimeError(
        "WOMPI_ENCRYPTION_KEY no es una llave Fernet valida. Generala con:\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    ) from e


def encriptar(texto_plano: str) -> str:
    """Encripta un texto (ej. una llave de Wompi) para guardarlo en la base de datos."""
    return _fernet.encrypt(texto_plano.encode()).decode()


def desencriptar(texto_encriptado: str) -> str:
    """
    Desencripta un valor guardado con `encriptar`. Lanza ValueError si el
    valor fue alterado (tampering) o no fue encriptado con esta misma llave
    maestra (ej. WOMPI_ENCRYPTION_KEY cambio despues de guardarlo).
    """
    try:
        return _fernet.decrypt(texto_encriptado.encode()).decode()
    except InvalidToken as e:
        raise ValueError("No se pudo desencriptar: el valor fue alterado o la llave maestra cambio.") from e
