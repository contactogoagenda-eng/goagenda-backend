"""
Gestion de las credenciales de Wompi de cada negocio (multi-tenant).

Cada negocio guarda su propio par de llaves de Wompi (publica, privada, de
eventos) en la tabla wompi_credentials, SIEMPRE encriptadas con
services/wompi_encryption.py antes de tocar la base de datos. Nunca se leen
credenciales de Wompi desde variables de entorno: la unica variable de
entorno relacionada es WOMPI_ENCRYPTION_KEY (la llave maestra que encripta/
desencripta lo que hay en la base de datos), ver services/wompi_encryption.py.
"""

from datetime import datetime, timezone

from services.db import supabase
from services.wompi_client import WompiError, obtener_info_comercio
from services.wompi_encryption import desencriptar, encriptar


def _registrar_auditoria(
    business_id: str,
    user_id: str | None,
    action: str,
    sandbox_mode_before: bool | None = None,
    sandbox_mode_after: bool | None = None,
    description: str = "",
) -> None:
    """Deja constancia de un cambio sobre las credenciales de Wompi de un negocio (nunca las llaves en si)."""
    try:
        supabase.table("wompi_credentials_audit_log").insert(
            {
                "business_id": business_id,
                "user_id": user_id,
                "action": action,
                "sandbox_mode_before": sandbox_mode_before,
                "sandbox_mode_after": sandbox_mode_after,
                "description": description,
            }
        ).execute()
    except Exception as e:
        # La auditoria no debe tumbar la operacion principal (guardar/probar credenciales).
        print(f"No se pudo registrar la auditoria de credenciales Wompi ({action}) para {business_id}: {e}")


def obtener_estado_credenciales(business_id: str) -> dict | None:
    """
    Trae el estado de configuracion de un negocio SIN desencriptar ni
    exponer las llaves (para mostrar en el panel de Ajustes). Retorna None
    si el negocio nunca configuro Wompi.
    """
    respuesta = (
        supabase.table("wompi_credentials")
        .select("id, sandbox_mode, is_configured, merchant_id, last_tested_at, test_result, created_at, updated_at")
        .eq("business_id", business_id)
        .execute()
    )
    if not respuesta.data:
        return None
    return respuesta.data[0]


def _obtener_credenciales_desencriptadas(business_id: str) -> dict | None:
    """
    Trae y desencripta las credenciales completas de un negocio. Uso
    INTERNO exclusivamente (crear links de pago, validar contra Wompi,
    verificar firma de webhooks) - nunca se debe devolver el resultado de
    esta funcion directamente en una respuesta HTTP.
    """
    respuesta = supabase.table("wompi_credentials").select("*").eq("business_id", business_id).execute()
    if not respuesta.data:
        return None

    fila = respuesta.data[0]
    return {
        **fila,
        "public_key": desencriptar(fila["public_key_encrypted"]),
        "private_key": desencriptar(fila["private_key_encrypted"]),
        "events_key": desencriptar(fila["events_key_encrypted"]),
    }


def guardar_credenciales(
    business_id: str,
    public_key: str,
    private_key: str,
    events_key: str,
    sandbox_mode: bool,
    user_id: str,
) -> dict:
    """
    Encripta y guarda (crea o reemplaza) las credenciales de Wompi de un
    negocio. No valida contra la API de Wompi (para eso ver
    validar_credenciales) - solo persiste, para no bloquear el guardado si
    Wompi esta lento o caido en ese momento.
    """
    existente = obtener_estado_credenciales(business_id)

    fila = {
        "business_id": business_id,
        "public_key_encrypted": encriptar(public_key.strip()),
        "private_key_encrypted": encriptar(private_key.strip()),
        "events_key_encrypted": encriptar(events_key.strip()),
        "sandbox_mode": sandbox_mode,
        "is_configured": True,
        "updated_by": user_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        # Un cambio de credenciales invalida el ultimo resultado de test.
        "last_tested_at": None,
        "test_result": None,
    }

    if existente:
        respuesta = supabase.table("wompi_credentials").update(fila).eq("business_id", business_id).execute()
        _registrar_auditoria(
            business_id,
            user_id,
            "UPDATED",
            sandbox_mode_before=existente.get("sandbox_mode"),
            sandbox_mode_after=sandbox_mode,
            description="Credenciales de Wompi actualizadas",
        )
    else:
        fila["created_by"] = user_id
        respuesta = supabase.table("wompi_credentials").insert(fila).execute()
        _registrar_auditoria(
            business_id,
            user_id,
            "CREATED",
            sandbox_mode_after=sandbox_mode,
            description="Credenciales de Wompi configuradas por primera vez",
        )

    fila_guardada = respuesta.data[0]
    fila_guardada.pop("public_key_encrypted", None)
    fila_guardada.pop("private_key_encrypted", None)
    fila_guardada.pop("events_key_encrypted", None)
    return fila_guardada


def validar_credenciales(business_id: str, user_id: str | None = None) -> tuple[bool, str]:
    """
    Prueba las credenciales guardadas contra la API real de Wompi
    (GET /merchants/info) y actualiza last_tested_at/test_result. Retorna
    (exito, mensaje) para mostrarle al dueño del negocio en el panel.
    """
    credenciales = _obtener_credenciales_desencriptadas(business_id)
    if not credenciales:
        return False, "No hay credenciales de Wompi configuradas para este negocio."

    try:
        info = obtener_info_comercio(credenciales["public_key"], credenciales["sandbox_mode"])
        mensaje = "Credenciales validas."
        exito = True
        merchant_id = str(info.get("id")) if info.get("id") else None
    except WompiError as e:
        mensaje = str(e)
        exito = False
        merchant_id = None

    supabase.table("wompi_credentials").update(
        {
            "last_tested_at": datetime.now(timezone.utc).isoformat(),
            "test_result": mensaje,
            **({"merchant_id": merchant_id} if merchant_id else {}),
        }
    ).eq("business_id", business_id).execute()

    _registrar_auditoria(business_id, user_id, "TESTED", description=mensaje)
    return exito, mensaje


def eliminar_credenciales(business_id: str, user_id: str | None = None) -> None:
    """Elimina la configuracion de Wompi de un negocio (deja de poder generar links de pago)."""
    existente = obtener_estado_credenciales(business_id)
    supabase.table("wompi_credentials").delete().eq("business_id", business_id).execute()
    _registrar_auditoria(
        business_id,
        user_id,
        "DELETED",
        sandbox_mode_before=existente.get("sandbox_mode") if existente else None,
        description="Credenciales de Wompi eliminadas",
    )


def obtener_credenciales_para_pago(business_id: str) -> dict | None:
    """
    Punto de entrada para el resto del sistema (crear_cita, endpoints de
    pago) cuando necesita las credenciales YA desencriptadas para llamar a
    Wompi. Retorna None si el negocio no tiene Wompi configurado, para que
    el caller decida como degradar (ej. no ofrecer el link de pago).
    """
    credenciales = _obtener_credenciales_desencriptadas(business_id)
    if not credenciales or not credenciales.get("is_configured"):
        return None
    return credenciales
