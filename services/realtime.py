"""
Notificaciones de citas en tiempo real via WebSocket.

Mantiene, en memoria, las conexiones WebSocket activas del panel (una o mas
por negocio, ya que el mismo dueño/empleado puede tener varias pestañas o
dispositivos abiertos) y permite difundir (`emitir_evento_cita`) un evento a
todas ellas cuando una cita se crea, se actualiza o se cancela, sin importar
si esa cita vino del agente de IA, del panel (creacion manual), o de un
webhook de WhatsApp/Supabase.

La mayoria de los puntos donde se crea/cancela/reprograma una cita hoy son
funciones sync (`def`, no `async def`) — Starlette las corre en un
threadpool, no en el event loop principal — asi que no pueden hacer
`await` directamente. `GestorConexionesTiempoReal.emitir` resuelve esto
programando el envio en el loop principal via `run_coroutine_threadsafe`,
registrado una vez al arrancar la app (ver `main.py`).
"""

import asyncio
import json
from typing import Any

from fastapi import WebSocket


class GestorConexionesTiempoReal:
    def __init__(self) -> None:
        self._conexiones: dict[str, set[WebSocket]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def registrar_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Llamado una vez en el evento `startup` de FastAPI (main.py)."""
        self._loop = loop

    async def aceptar(self, websocket: WebSocket) -> None:
        """
        Solo acepta el handshake del WebSocket, sin unirlo todavia al grupo
        de difusion del negocio. Se separa de `registrar` para que el
        cliente pueda enviar su primer mensaje (la autenticacion) antes de
        empezar a recibir eventos de citas de ese negocio: si se uniera al
        grupo antes de validar el token, una conexion aun no autenticada
        podria recibir datos de clientes (nombre, telefono, servicio) si un
        evento real llega durante la ventana de espera de la autenticacion.
        """
        await websocket.accept()

    def registrar(self, business_id: str, websocket: WebSocket) -> None:
        """Une la conexion (ya aceptada y autenticada) al grupo de difusion del negocio."""
        self._conexiones.setdefault(business_id, set()).add(websocket)

    def desconectar(self, business_id: str, websocket: WebSocket) -> None:
        conexiones = self._conexiones.get(business_id)
        if not conexiones:
            return
        conexiones.discard(websocket)
        if not conexiones:
            self._conexiones.pop(business_id, None)

    async def _difundir(self, business_id: str, mensaje: dict[str, Any]) -> None:
        conexiones = list(self._conexiones.get(business_id, ()))
        if not conexiones:
            return

        payload = json.dumps(mensaje)
        muertas: list[WebSocket] = []

        for ws in conexiones:
            try:
                await ws.send_text(payload)
            except Exception:
                muertas.append(ws)

        for ws in muertas:
            self.desconectar(business_id, ws)

    def emitir(self, business_id: str, mensaje: dict[str, Any]) -> None:
        """
        Punto de entrada seguro para llamar tanto desde codigo async como
        desde codigo sync (agent/tools.py, manual_appointments.py,
        webhook.py): no bloquea ni requiere que el caller sea una coroutine.
        No hace nada si aun no hay loop registrado o no hay conexiones para
        ese negocio (evita trabajo de mas en el caso comun sin paneles
        conectados).
        """
        destinatarios = len(self._conexiones.get(business_id, ()))
        print(f"[ws] evento {mensaje.get('type')} business={business_id} conexiones={destinatarios}", flush=True)

        if self._loop is None or not destinatarios:
            return

        try:
            asyncio.run_coroutine_threadsafe(self._difundir(business_id, mensaje), self._loop)
        except RuntimeError:
            pass


gestor_tiempo_real = GestorConexionesTiempoReal()


def emitir_evento_cita(tipo: str, business_id: str, appointment_id: str) -> None:
    """
    Helper de alto nivel para los puntos donde se crea/cancela/reprograma
    una cita: vuelve a leer la cita completa (con los joins de servicio y
    empleado) para que el payload tenga la misma forma que ya usa el
    frontend en GET /appointments (`AppointmentApiRecord`), sin importar
    que datos parciales tenia a mano el caller. `tipo` es uno de
    "appointment.created", "appointment.updated", "appointment.cancelled".
    """
    try:
        from services.db import get_appointment_full

        cita = get_appointment_full(appointment_id)
        if not cita:
            return

        gestor_tiempo_real.emitir(business_id, {"type": tipo, "business_id": business_id, "appointment": cita})
    except Exception as e:
        print(f"No se pudo emitir el evento de tiempo real ({tipo}): {e}")
