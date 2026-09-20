import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from services.auth import verificar_acceso_negocio
from services.db import supabase
from services.realtime import gestor_tiempo_real

router = APIRouter(tags=["realtime"])


def _resolver_usuario(token: str | None) -> str | None:
    """Valida un JWT de Supabase Auth y devuelve el user_id, o None si no es valido."""
    if not token:
        return None
    try:
        respuesta = supabase.auth.get_user(token)
        return respuesta.user.id if respuesta.user else None
    except Exception:
        return None


@router.websocket("/ws/appointments")
async def ws_appointments(websocket: WebSocket, business_id: str):
    """
    Notificaciones de citas en tiempo real para el panel del negocio
    (Angular). Contrato:

    1. El cliente conecta a `/ws/appointments?business_id=<id>`.
    2. Como primer mensaje debe enviar `{"type": "auth", "token": "<jwt>"}`
       con el JWT de Supabase Auth de la sesion (el mismo que ya manda como
       `Authorization: Bearer` en las llamadas REST). Si no llega en 10
       segundos, o el token es invalido, o el usuario no tiene acceso a
       `business_id` (ni dueño ni empleado activo), el servidor cierra la
       conexion.
    3. Tras autenticar, el servidor no espera nada mas del cliente (ignora
       cualquier mensaje adicional; solo sigue leyendo para detectar la
       desconexion) y empuja un mensaje JSON por cada evento de cita del
       negocio:
       `{"type": "appointment.created" | "appointment.updated" | "appointment.cancelled",
         "business_id": "...", "appointment": {...con el mismo shape que GET /appointments...}}`

    Ver `services/realtime.py` (el gestor de conexiones y `emitir_evento_cita`,
    llamado desde `agent/tools.py`, `routes/manual_appointments.py` y
    `routes/webhook.py`).
    """
    await gestor_tiempo_real.aceptar(websocket)

    try:
        try:
            primer_mensaje = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        except asyncio.TimeoutError:
            print(f"[ws] cierre 4408 (sin auth en 10s) business={business_id}", flush=True)
            await websocket.close(code=4408)
            return

        try:
            datos = json.loads(primer_mensaje)
        except ValueError:
            print(f"[ws] cierre 4400 (JSON invalido) business={business_id}", flush=True)
            await websocket.close(code=4400)
            return

        token = datos.get("token") if isinstance(datos, dict) else None
        user_id = _resolver_usuario(token)

        if not user_id:
            print(f"[ws] cierre 4401 (token invalido) business={business_id}", flush=True)
            await websocket.close(code=4401)
            return

        try:
            verificar_acceso_negocio(business_id, user_id)
        except HTTPException:
            print(f"[ws] cierre 4403 (sin acceso) business={business_id} user={user_id}", flush=True)
            await websocket.close(code=4403)
            return

        # Solo hasta aqui, con el token validado y el acceso al negocio
        # confirmado, se une la conexion al grupo que recibe sus eventos.
        gestor_tiempo_real.registrar(business_id, websocket)
        print(f"[ws] autenticado business={business_id} user={user_id}", flush=True)
        await websocket.send_text(json.dumps({"type": "auth_ok"}))

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        gestor_tiempo_real.desconectar(business_id, websocket)
