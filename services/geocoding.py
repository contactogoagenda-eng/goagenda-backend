"""
Geocodificacion de direcciones con Nominatim (OpenStreetMap) - gratuito, sin
llave de API. Se usa en el flujo de domicilios (ver
agent/tools.py:_resolver_zona) para inferir automaticamente en que
municipio/barrio esta el cliente a partir de su direccion real, sin
depender de que la diga por su nombre exacto de zona.

Un fallo aqui (direccion no encontrada, Nominatim lento o caido) NUNCA debe
bloquear el agendamiento - simplemente se pierde la inferencia automatica y
el flujo sigue con el matching por texto de siempre (ver
agent/tools.py:_mejor_coincidencia_zona). geocodificar() esta diseñada para
no lanzar excepciones, solo retornar None en cualquier caso de falla.

Politica de uso de Nominatim (https://operations.osmfoundation.org/policies/nominatim/):
maximo 1 request/segundo y un User-Agent que identifique la aplicacion -
Nominatim bloquea o degrada clientes que no cumplen esto. Este modulo
serializa las llamadas para respetar ese limite (con un pequeño margen) y
cachea resultados en memoria del proceso para no repetir la misma consulta
dos veces en la misma conversacion (la direccion se valida tanto en
pedir_confirmacion_cita como en crear_cita).

Para volumen alto (muchas citas a domicilio por minuto) esto dejaria de ser
suficiente - en ese punto conviene un proveedor de pago (Google, Mapbox,
LocationIQ) o un Nominatim propio (self-hosted), sin cambiar la interfaz de
este modulo.
"""

import time
from threading import Lock

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "GoAgenda/1.0 (+https://api.goagenda.online)"
_TIMEOUT = httpx.Timeout(6.0, connect=4.0)
_INTERVALO_MINIMO_SEGUNDOS = 1.05  # Nominatim exige max 1 req/seg; un poco de margen.

# Campos de "address" de Nominatim, de mas especifico a mas general - los
# que sirven para matchear contra zonas configuradas (barrio, pueblo,
# municipio, region). Ver localidades_de().
_CAMPOS_LOCALIDAD = [
    "suburb",
    "neighbourhood",
    "quarter",
    "city",
    "town",
    "village",
    "municipality",
    "county",
    "state_district",
]

_cache: dict[str, dict | None] = {}
_cache_lock = Lock()
_ultima_llamada = 0.0
_llamada_lock = Lock()


def _esperar_rate_limit() -> None:
    """Bloquea (sleep) lo necesario para no superar 1 request/segundo a Nominatim, sin importar cuantos hilos llamen a la vez."""
    global _ultima_llamada
    with _llamada_lock:
        transcurrido = time.monotonic() - _ultima_llamada
        if transcurrido < _INTERVALO_MINIMO_SEGUNDOS:
            time.sleep(_INTERVALO_MINIMO_SEGUNDOS - transcurrido)
        _ultima_llamada = time.monotonic()


def geocodificar(direccion: str, pais: str = "co") -> dict | None:
    """
    Geocodifica una direccion con Nominatim. Retorna el dict "address" que
    devuelve la API (con los campos que existan para ese lugar: city, town,
    village, suburb, county, etc) o None si no se encontro nada, si hubo un
    error de red, o si la respuesta no se pudo interpretar - nunca lanza
    excepcion, es seguro llamarla sin try/except desde el caller.
    """
    clave_cache = " ".join(direccion.strip().lower().split())
    if not clave_cache:
        return None

    with _cache_lock:
        if clave_cache in _cache:
            return _cache[clave_cache]

    resultado = None
    try:
        _esperar_rate_limit()
        respuesta = httpx.get(
            NOMINATIM_URL,
            params={"q": direccion, "format": "jsonv2", "addressdetails": 1, "countrycodes": pais, "limit": 1},
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
        if respuesta.status_code == 200:
            datos = respuesta.json()
            if datos:
                resultado = datos[0].get("address")
    except (httpx.HTTPError, ValueError) as e:
        print(f"No se pudo geocodificar la direccion '{direccion}': {e}")
        resultado = None

    with _cache_lock:
        _cache[clave_cache] = resultado
    return resultado


def localidades_de(direccion_geocodificada: dict) -> list[str]:
    """Nombres de lugar (barrio, ciudad, municipio...) de una respuesta de geocodificar(), de mas a menos especifico."""
    return [direccion_geocodificada[c] for c in _CAMPOS_LOCALIDAD if direccion_geocodificada.get(c)]
