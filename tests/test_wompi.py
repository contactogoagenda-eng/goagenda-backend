"""Cadena de pagos: encriptacion, cliente de Wompi, credenciales, solicitudes de abono, confirmacion de citas y webhook."""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from services import appointment_confirmation as ac
from services import wompi_client as wc
from services import wompi_credentials as cred
from services import wompi_encryption as enc
from services import wompi_payment_requests as wpr

BIZ = "biz-1"


# ------------------------------------------------------------------------------
# Encriptacion
# ------------------------------------------------------------------------------

def test_encriptar_y_desencriptar():
    secreto = enc.encriptar("prv_test_123")
    assert secreto != "prv_test_123" and enc.desencriptar(secreto) == "prv_test_123"


def test_desencriptar_valor_alterado():
    with pytest.raises(ValueError, match="alterado"):
        enc.desencriptar(enc.encriptar("x")[:-4] + "AAAA")


@pytest.mark.parametrize("llave, mensaje", [("", "Falta WOMPI_ENCRYPTION_KEY"), ("no-es-fernet", "no es una llave Fernet")])
def test_llave_maestra_faltante_o_invalida(monkeypatch, llave, mensaje):
    import importlib

    monkeypatch.setenv("WOMPI_ENCRYPTION_KEY", llave)
    with pytest.raises(RuntimeError, match=mensaje):
        importlib.reload(enc)
    monkeypatch.undo()
    importlib.reload(enc)


# ------------------------------------------------------------------------------
# Cliente HTTP de Wompi
# ------------------------------------------------------------------------------

class HttpFalso:
    def __init__(self, respuesta=None, error=None):
        self.respuesta, self.error, self.llamadas = respuesta, error, []

    def __call__(self, url, **kwargs):
        self.llamadas.append((url, kwargs))
        if self.error:
            raise self.error
        return self.respuesta


def respuesta(status, data=None, texto=None):
    if texto is not None:
        return httpx.Response(status, text=texto)
    return httpx.Response(status, json={"data": data or {}})


def test_info_comercio(monkeypatch):
    falso = HttpFalso(respuesta(200, {"id": 99}))
    monkeypatch.setattr(wc.httpx, "get", falso)
    assert wc.obtener_info_comercio("pub_test", sandbox_mode=True) == {"id": 99}
    assert falso.llamadas[0][0] == "https://sandbox.wompi.co/v1/merchants/info"


def test_info_comercio_errores(monkeypatch):
    monkeypatch.setattr(wc.httpx, "get", HttpFalso(respuesta(401)))
    with pytest.raises(wc.WompiError) as e:
        wc.obtener_info_comercio("pub", sandbox_mode=False)
    assert e.value.status_code == 401 and "Produccion" in str(e.value)
    monkeypatch.setattr(wc.httpx, "get", HttpFalso(error=httpx.ConnectError("caido")))
    with pytest.raises(wc.WompiError, match="No se pudo conectar"):
        wc.obtener_info_comercio("pub", sandbox_mode=True)


def test_crear_link_de_pago(monkeypatch):
    falso = HttpFalso(respuesta(201, {"id": "link-1"}))
    monkeypatch.setattr(wc.httpx, "post", falso)
    antes = datetime.now(timezone.utc)
    assert wc.crear_link_de_pago("prv", False, "Abono", "", 1500000, 1.0, "http://frontend.test/chat/x") == {"id": "link-1"}
    url, kwargs = falso.llamadas[0]
    assert url == "https://production.wompi.co/v1/payment_links"
    payload = kwargs["json"]
    assert payload["description"] == "Abono" and payload["redirect_url"] == "http://frontend.test/chat/x"
    expira = datetime.strptime(payload["expires_at"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    # Bug historico: se restaban 5 horas y Wompi rechazaba el link (expires_at en el pasado).
    assert timedelta(minutes=59) < expira - antes < timedelta(minutes=61)


def test_crear_link_sin_expiracion_ni_redirect(monkeypatch):
    falso = HttpFalso(respuesta(200, {"id": "link-1"}))
    monkeypatch.setattr(wc.httpx, "post", falso)
    wc.crear_link_de_pago("prv", True, "Abono", "Desc", 100, expira_en_horas=None)
    payload = falso.llamadas[0][1]["json"]
    assert "expires_at" not in payload and "redirect_url" not in payload


def test_crear_link_errores(monkeypatch):
    monkeypatch.setattr(wc.httpx, "post", HttpFalso(respuesta(422, texto="expires_at invalido")))
    with pytest.raises(wc.WompiError, match="expires_at invalido"):
        wc.crear_link_de_pago("prv", True, "Abono", "", 100)
    monkeypatch.setattr(wc.httpx, "post", HttpFalso(error=httpx.ReadTimeout("lento")))
    with pytest.raises(wc.WompiError, match="No se pudo conectar"):
        wc.crear_link_de_pago("prv", True, "Abono", "", 100)


def test_consultar_transaccion(monkeypatch):
    monkeypatch.setattr(wc.httpx, "get", HttpFalso(respuesta(200, {"id": "tx", "status": "APPROVED"})))
    assert wc.consultar_transaccion("prv", True, "tx")["status"] == "APPROVED"
    for status, texto in [(404, "no encontrada"), (500, "HTTP 500")]:
        monkeypatch.setattr(wc.httpx, "get", HttpFalso(respuesta(status)))
        with pytest.raises(wc.WompiError, match=texto):
            wc.consultar_transaccion("prv", True, "tx")
    monkeypatch.setattr(wc.httpx, "get", HttpFalso(error=httpx.ConnectError("x")))
    with pytest.raises(wc.WompiError):
        wc.consultar_transaccion("prv", True, "tx")


def test_url_de_checkout():
    assert wc.construir_url_checkout("abc") == "https://checkout.wompi.co/l/abc"


# ------------------------------------------------------------------------------
# Credenciales por negocio
# ------------------------------------------------------------------------------

def test_guardar_credenciales_encripta_y_no_devuelve_las_llaves(db):
    fila = cred.guardar_credenciales(BIZ, " pub_test ", "prv_test", "evt_test", True, "user-1")
    assert not any(k.endswith("_encrypted") for k in fila)
    guardada = db.filas("wompi_credentials")[0]
    assert guardada["public_key_encrypted"] != "pub_test" and enc.desencriptar(guardada["public_key_encrypted"]) == "pub_test"
    assert db.filas("wompi_credentials_audit_log")[0]["action"] == "CREATED"

    cred.guardar_credenciales(BIZ, "pub2", "prv2", "evt2", False, "user-1")
    assert len(db.filas("wompi_credentials")) == 1
    auditoria = db.filas("wompi_credentials_audit_log")[1]
    assert auditoria["action"] == "UPDATED" and auditoria["sandbox_mode_before"] is True and auditoria["sandbox_mode_after"] is False


def test_auditoria_que_falla_no_tumba_el_guardado(db, capsys):
    db.fallar_en["wompi_credentials_audit_log"] = RuntimeError("caido")
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "user-1")
    assert len(db.filas("wompi_credentials")) == 1
    assert "No se pudo registrar la auditoria" in capsys.readouterr().out


def test_estado_y_credenciales_para_pago(db):
    assert cred.obtener_estado_credenciales(BIZ) is None
    assert cred.obtener_credenciales_para_pago(BIZ) is None
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")
    assert cred.obtener_estado_credenciales(BIZ)["is_configured"] is True
    para_pago = cred.obtener_credenciales_para_pago(BIZ)
    assert (para_pago["public_key"], para_pago["private_key"], para_pago["events_key"]) == ("pub", "prv", "evt")
    db.filas("wompi_credentials")[0]["is_configured"] = False
    assert cred.obtener_credenciales_para_pago(BIZ) is None


def test_validar_credenciales(db, monkeypatch):
    assert cred.validar_credenciales(BIZ) == (False, "No hay credenciales de Wompi configuradas para este negocio.")
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")
    monkeypatch.setattr(cred, "obtener_info_comercio", lambda pub, sandbox: {"id": 123})
    assert cred.validar_credenciales(BIZ, "u") == (True, "Credenciales validas.")
    fila = db.filas("wompi_credentials")[0]
    assert fila["merchant_id"] == "123" and fila["test_result"] == "Credenciales validas."

    def rechaza(pub, sandbox):
        raise wc.WompiError("Wompi rechazo la llave publica")

    monkeypatch.setattr(cred, "obtener_info_comercio", rechaza)
    assert cred.validar_credenciales(BIZ) == (False, "Wompi rechazo la llave publica")
    assert db.filas("wompi_credentials")[0]["merchant_id"] == "123"  # no se borra el ultimo valido


def test_eliminar_credenciales(db):
    cred.eliminar_credenciales(BIZ)  # sin credenciales: igual audita
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", False, "u")
    cred.eliminar_credenciales(BIZ, "u")
    assert db.filas("wompi_credentials") == []
    assert db.filas("wompi_credentials_audit_log")[-1]["sandbox_mode_before"] is False


# ------------------------------------------------------------------------------
# Solicitudes de abono
# ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "servicio, centavos",
    [
        ({"requires_payment": False}, None),
        ({"requires_payment": True, "payment_type": "percentage", "payment_percentage": 50, "price": 15000}, 750000),
        ({"requires_payment": True, "payment_type": "percentage", "payment_percentage": None, "price": 15000}, None),
        ({"requires_payment": True, "payment_type": "fixed", "payment_fixed_amount_cents": 2000000}, 2000000),
        ({"requires_payment": True, "payment_type": "fixed", "payment_fixed_amount_cents": None}, None),
        ({"requires_payment": True, "payment_type": "otro"}, None),
    ],
)
def test_calcular_monto_abono(servicio, centavos):
    assert wpr.calcular_monto_abono_cents(servicio) == centavos


SERVICIO_ABONO = {"id": "s1", "name": "Tinte", "requires_payment": True, "payment_type": "fixed", "payment_fixed_amount_cents": 2000000}


@pytest.fixture
def wompi_configurado(db, monkeypatch):
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")
    crear = MagicMock(return_value={"id": "link-1"})
    monkeypatch.setattr(wpr, "crear_link_de_pago", crear)
    return crear


def test_crear_solicitud_pago(db, wompi_configurado):
    r = wpr.crear_solicitud_pago(BIZ, SERVICIO_ABONO, "sess-1", "573001", appointment_id="cita-1", employee_id="emp-1", expira_en_horas=1)
    assert r["checkout_url"] == "https://checkout.wompi.co/l/link-1" and r["amount_in_cents"] == 2000000
    assert r["description"] == "Abono para Tinte" and r["request_id"]
    assert wompi_configurado.call_args.kwargs["redirect_url"] == f"http://frontend.test/chat/{BIZ}/emp-1"
    fila = db.filas("wompi_payment_requests")[0]
    assert fila["status"] == "pending" and fila["appointment_id"] == "cita-1" and fila["session_id"] == "sess-1"


def test_crear_solicitud_redirect_al_chat_general(db, wompi_configurado):
    wpr.crear_solicitud_pago(BIZ, {**SERVICIO_ABONO, "payment_description": "Separa tu cupo"}, None, None)
    assert wompi_configurado.call_args.kwargs["redirect_url"] == f"http://frontend.test/chat/{BIZ}"
    assert wompi_configurado.call_args.kwargs["descripcion"] == "Separa tu cupo"


def test_crear_solicitud_casos_sin_link(db, monkeypatch, capsys):
    assert wpr.crear_solicitud_pago(BIZ, {"requires_payment": False}, None, None) is None
    assert wpr.crear_solicitud_pago(BIZ, SERVICIO_ABONO, None, None) is None  # sin Wompi configurado
    assert "no tiene Wompi configurado" in capsys.readouterr().out
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")

    def falla(**kw):
        raise wc.WompiError("422")

    monkeypatch.setattr(wpr, "crear_link_de_pago", falla)
    assert wpr.crear_solicitud_pago(BIZ, SERVICIO_ABONO, None, None) is None
    monkeypatch.setattr(wpr, "crear_link_de_pago", lambda **kw: {})
    assert wpr.crear_solicitud_pago(BIZ, SERVICIO_ABONO, None, None) is None


def test_crear_solicitud_aunque_no_se_pueda_guardar(db, wompi_configurado, capsys):
    db.fallar_en["wompi_payment_requests"] = RuntimeError("caido")
    r = wpr.crear_solicitud_pago(BIZ, SERVICIO_ABONO, None, None)
    assert r["request_id"] is None and r["checkout_url"]
    assert "no se pudo guardar" in capsys.readouterr().out


def test_consultas_y_actualizacion_de_solicitudes(db):
    a = db.sembrar("wompi_payment_requests", {"business_id": BIZ, "wompi_payment_link_id": "l1", "status": "pending", "created_at": "2026-01-01"})
    db.sembrar("wompi_payment_requests", {"business_id": BIZ, "wompi_payment_link_id": "l2", "status": "paid", "created_at": "2026-02-01"})
    assert wpr.obtener_solicitud_por_payment_link(BIZ, "l1")["id"] == a["id"]
    assert wpr.obtener_solicitud_por_payment_link(BIZ, "x") is None
    assert wpr.obtener_solicitud_por_id(BIZ, a["id"])["id"] == a["id"]
    assert wpr.obtener_solicitud_por_id("otro", a["id"]) is None
    assert [s["wompi_payment_link_id"] for s in wpr.listar_solicitudes_pago(BIZ)] == ["l2", "l1"]
    assert len(wpr.listar_solicitudes_pago(BIZ, status="pending")) == 1

    wpr.actualizar_estado_transaccion(a["id"], "tx-1", "DECLINED")
    assert db.filas("wompi_payment_requests")[0]["status"] == "pending"
    assert wpr.actualizar_estado_transaccion(a["id"], "tx-2", "APPROVED")["status"] == "paid"
    assert wpr.actualizar_estado_transaccion("x", "tx", "APPROVED") is None


@pytest.fixture
def efectos_cita(monkeypatch):
    from services import realtime

    efectos = MagicMock()
    monkeypatch.setattr(ac, "enviar_notificacion_nueva_cita", efectos.push)
    monkeypatch.setattr(ac, "enviar_confirmacion_cita_cliente", efectos.whatsapp)
    monkeypatch.setattr(realtime, "emitir_evento_cita", efectos.evento)
    return efectos


def test_confirmar_cita_desde_pago(db, efectos_cita):
    assert wpr.confirmar_cita_desde_pago(BIZ, {}) == {"cita": None, "conflicto": False}
    assert wpr.confirmar_cita_desde_pago(BIZ, {"appointment_id": "no-existe"}) == {"cita": None, "conflicto": True}
    base = {"business_id": BIZ, "client_phone": "573", "scheduled_at": "2026-10-10T10:00:00"}
    cancelada = db.sembrar("appointments", {**base, "status": "cancelled"})
    assert wpr.confirmar_cita_desde_pago(BIZ, {"appointment_id": cancelada["id"]})["conflicto"] is True
    confirmada = db.sembrar("appointments", {**base, "status": "confirmed"})
    assert wpr.confirmar_cita_desde_pago(BIZ, {"appointment_id": confirmada["id"]})["conflicto"] is False
    pendiente = db.sembrar("appointments", {**base, "status": "pending_payment"})
    r = wpr.confirmar_cita_desde_pago(BIZ, {"appointment_id": pendiente["id"]})
    assert r["conflicto"] is False and r["cita"]["status"] == "confirmed"


@pytest.fixture
def efectos_pago(monkeypatch):
    from agent import graph
    from services import scheduling

    efectos = MagicMock()
    monkeypatch.setattr(graph, "enviar_notificacion_sistema", efectos.chat)
    monkeypatch.setattr(wpr, "enviar_notificacion_pago_recibido", efectos.push)
    monkeypatch.setattr(wpr.gestor_tiempo_real, "emitir", efectos.evento)
    monkeypatch.setattr(scheduling, "ahora_local", lambda: datetime(2026, 10, 7, 8, 0))
    return efectos


def test_notificar_pago_con_cita(db, efectos_pago):
    db.sembrar("businesses", {"id": BIZ, "fcm_token": "fcm"})
    cita = {"id": "c1", "scheduled_at": "2026-10-10T10:00:00", "services": {"name": "Tinte"}}
    wpr.notificar_pago_confirmado(BIZ, {"session_id": "s1", "description": "Abono"}, 750000, cita=cita)
    mensaje = efectos_pago.chat.call_args.args[2]
    assert "*$7.500*" in mensaje and "Servicio: *Tinte*" in mensaje and "sabado 10 de octubre a las 10:00 am" in mensaje
    efectos_pago.push.assert_called_once_with(fcm_token="fcm", monto_texto="$7.500", descripcion="Abono")
    assert efectos_pago.evento.call_args.args[1]["appointment_id"] == "c1"


@pytest.mark.parametrize("conflicto, fragmento", [(True, "ya no estaba disponible"), (False, "Tu cita ya esta asegurada")])
def test_notificar_pago_sin_cita_o_con_conflicto(db, efectos_pago, conflicto, fragmento):
    wpr.notificar_pago_confirmado(BIZ, {"session_id": "s1", "amount_in_cents": 500000}, None, conflicto=conflicto)
    assert fragmento in efectos_pago.chat.call_args.args[2] and "$5.000" in efectos_pago.chat.call_args.args[2]


def test_notificar_pago_tolera_fallas_de_cada_canal(db, efectos_pago, capsys):
    efectos_pago.chat.side_effect = RuntimeError("chat")
    efectos_pago.push.side_effect = RuntimeError("push")
    efectos_pago.evento.side_effect = RuntimeError("ws")
    wpr.notificar_pago_confirmado(BIZ, {"session_id": "s1"}, 100)
    salida = capsys.readouterr().out
    assert "insertar el mensaje de pago" in salida and "push de pago" in salida and "tiempo real de pago" in salida


def test_notificar_pago_sin_sesion_no_escribe_en_el_chat(db, efectos_pago):
    wpr.notificar_pago_confirmado(BIZ, {}, 100)
    efectos_pago.chat.assert_not_called()


class TestVerificarEstado:
    def _solicitud(self, db, **extra):
        return db.sembrar("wompi_payment_requests", {"business_id": BIZ, "status": "pending", **extra})

    def test_casos_sin_consulta_a_wompi(self, db):
        with pytest.raises(ValueError, match="no encontrada"):
            wpr.verificar_estado_solicitud(BIZ, "x")
        pagada = self._solicitud(db, status="paid")
        assert "ya estaba confirmado" in wpr.verificar_estado_solicitud(BIZ, pagada["id"])["mensaje"]
        sin_tx = self._solicitud(db)
        assert "ningun intento de pago" in wpr.verificar_estado_solicitud(BIZ, sin_tx["id"])["mensaje"]
        con_tx = self._solicitud(db, wompi_transaction_id="tx-1")
        with pytest.raises(ValueError, match="ya no tiene Wompi"):
            wpr.verificar_estado_solicitud(BIZ, con_tx["id"])

    def test_aprobada_confirma_y_notifica(self, db, monkeypatch):
        cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")
        solicitud = self._solicitud(db, wompi_transaction_id="tx-1", appointment_id="c1")
        monkeypatch.setattr(wpr, "consultar_transaccion", lambda prv, sb, tx: {"id": tx, "status": "APPROVED", "amount_in_cents": 100})
        monkeypatch.setattr(wpr, "confirmar_cita_desde_pago", lambda b, s: {"cita": {"id": "c1"}, "conflicto": False})
        notificar = MagicMock()
        monkeypatch.setattr(wpr, "notificar_pago_confirmado", notificar)
        r = wpr.verificar_estado_solicitud(BIZ, solicitud["id"])
        assert r["mensaje"] == "Estado actualizado: APPROVED." and r["solicitud"]["status"] == "paid"
        notificar.assert_called_once()

    def test_error_de_wompi(self, db, monkeypatch):
        cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")
        solicitud = self._solicitud(db, wompi_transaction_id="tx-1")

        def falla(*a):
            raise wc.WompiError("Transaccion no encontrada en Wompi.")

        monkeypatch.setattr(wpr, "consultar_transaccion", falla)
        with pytest.raises(ValueError, match="no encontrada"):
            wpr.verificar_estado_solicitud(BIZ, solicitud["id"])


def test_expirar_solicitudes_vencidas_libera_el_cupo(db):
    assert wpr.expirar_solicitudes_vencidas() == 0
    pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    futuro = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    hold = db.sembrar("appointments", {"status": "pending_payment"})
    ya_confirmada = db.sembrar("appointments", {"status": "confirmed"})
    db.sembrar("wompi_payment_requests",
               {"status": "pending", "expires_at": pasado, "appointment_id": hold["id"]},
               {"status": "pending", "expires_at": pasado, "appointment_id": ya_confirmada["id"]},
               {"status": "pending", "expires_at": pasado, "appointment_id": None},
               {"status": "pending", "expires_at": futuro},
               {"status": "paid", "expires_at": pasado})
    assert wpr.expirar_solicitudes_vencidas() == 3
    estados = {f["id"]: f["status"] for f in db.filas("appointments")}
    assert estados[hold["id"]] == "cancelled" and estados[ya_confirmada["id"]] == "confirmed"
    assert [f["status"] for f in db.filas("wompi_payment_requests")] == ["expired", "expired", "expired", "pending", "paid"]


# ------------------------------------------------------------------------------
# Creacion y confirmacion de citas (services/appointment_confirmation.py)
# ------------------------------------------------------------------------------

DATOS_CITA = dict(business_id=BIZ, client_phone="573001", client_name="Ana", service_id="s1",
                  scheduled_at="2026-10-10T10:00:00", employee_id="e1")


def test_finalizar_creacion_cita_notifica_todo(db, efectos_cita):
    db.sembrar("businesses", {"id": BIZ, "name": "Barberia", "fcm_token": "fcm"})
    cita = ac.finalizar_creacion_cita(service_name="Corte", **DATOS_CITA)
    assert cita["status"] == "confirmed"
    efectos_cita.push.assert_called_once()
    assert efectos_cita.whatsapp.call_args.kwargs["nombre_negocio"] == "Barberia"
    efectos_cita.evento.assert_called_once_with("appointment.created", BIZ, cita["id"])


def test_finalizar_creacion_tolera_fallas_de_notificacion(db, efectos_cita, capsys):
    efectos_cita.push.side_effect = RuntimeError("p")
    efectos_cita.whatsapp.side_effect = RuntimeError("w")
    efectos_cita.evento.side_effect = RuntimeError("e")
    assert ac.finalizar_creacion_cita(service_name="Corte", **DATOS_CITA)["id"]
    salida = capsys.readouterr().out
    assert "push" in salida and "WhatsApp" in salida and "tiempo real" in salida


def test_finalizar_creacion_sin_resultado(monkeypatch):
    monkeypatch.setattr(ac, "create_appointment", lambda **kw: [])
    assert ac.finalizar_creacion_cita(service_name="Corte", **DATOS_CITA) is None


def test_cita_pendiente_de_pago_no_notifica_y_detecta_carreras(db, efectos_cita):
    db.agregar_unico("appointments", ["employee_id", "scheduled_at"], lambda f: f["status"] in ("confirmed", "pending_payment"))
    cita = ac.crear_cita_pendiente_pago(**DATOS_CITA)
    assert cita["status"] == "pending_payment"
    efectos_cita.push.assert_not_called() and efectos_cita.evento.assert_not_called()
    assert ac.crear_cita_pendiente_pago(**DATOS_CITA) is None  # otro cliente tomo el mismo cupo


def test_cita_pendiente_otros_errores_si_se_propagan(monkeypatch):
    def falla(**kw):
        raise APIError({"code": "42501", "message": "permiso"})

    monkeypatch.setattr(ac, "create_appointment", falla)
    with pytest.raises(APIError):
        ac.crear_cita_pendiente_pago(**DATOS_CITA)
    monkeypatch.setattr(ac, "create_appointment", lambda **kw: [])
    assert ac.crear_cita_pendiente_pago(**DATOS_CITA) is None


def test_confirmar_cita_pendiente(db, efectos_cita, capsys):
    assert ac.confirmar_cita_pendiente("no-existe") is None
    cita = db.sembrar("appointments", {**DATOS_CITA, "status": "pending_payment", "services": {"name": "Tinte"}})
    confirmada = ac.confirmar_cita_pendiente(cita["id"])
    assert confirmada["status"] == "confirmed"
    assert efectos_cita.whatsapp.call_args.kwargs["nombre_servicio"] == "Tinte"
    efectos_cita.evento.assert_called_once_with("appointment.created", BIZ, cita["id"])

    efectos_cita.push.side_effect = efectos_cita.whatsapp.side_effect = efectos_cita.evento.side_effect = RuntimeError("x")
    ac.confirmar_cita_pendiente(cita["id"])
    assert capsys.readouterr().out.count("No se pudo") == 3


# ------------------------------------------------------------------------------
# Webhook de Wompi
# ------------------------------------------------------------------------------

from routes import wompi_webhook_routes as webhook  # noqa: E402


def evento(status="APPROVED", events_key="evt", evento_tipo="transaction.updated", link="link-1", alterar=False):
    data = {"transaction": {"id": "tx-1", "status": status, "payment_link_id": link, "amount_in_cents": 750000}}
    props = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    timestamp = 1700000000
    cadena = "".join(str(webhook._valor_por_ruta(data, p)) for p in props) + str(timestamp) + events_key
    checksum = hashlib.sha256(cadena.encode()).hexdigest()
    if alterar:
        data["transaction"]["amount_in_cents"] = 1
    return {"event": evento_tipo, "data": data, "timestamp": timestamp, "signature": {"properties": props, "checksum": checksum}}


@pytest.fixture
def cliente_webhook(db, monkeypatch):
    app = FastAPI()
    app.include_router(webhook.router)
    cred.guardar_credenciales(BIZ, "pub", "prv", "evt", True, "u")
    efectos = MagicMock()
    efectos.confirmar.return_value = {"cita": {"id": "c1"}, "conflicto": False}
    monkeypatch.setattr(webhook, "confirmar_cita_desde_pago", efectos.confirmar)
    monkeypatch.setattr(webhook, "notificar_pago_confirmado", efectos.notificar)
    return TestClient(app), efectos


def test_valor_por_ruta():
    assert webhook._valor_por_ruta({"a": {"b": 1}}, "a.b") == 1
    assert webhook._valor_por_ruta({"a": 1}, "a.b") is None
    assert webhook._valor_por_ruta({}, "x") is None


def test_firma_valida():
    assert webhook._firma_valida(evento(), "evt") is True
    assert webhook._firma_valida(evento(), "otra-llave") is False
    assert webhook._firma_valida(evento(alterar=True), "evt") is False
    assert webhook._firma_valida({"signature": {}}, "evt") is False
    assert webhook._firma_valida(evento(), "") is False


def test_webhook_aprobado_confirma_la_cita(db, cliente_webhook):
    cliente, efectos = cliente_webhook
    db.sembrar("wompi_payment_requests", {"business_id": BIZ, "wompi_payment_link_id": "link-1", "status": "pending"})
    r = cliente.post(f"/wompi/webhooks/{BIZ}", json=evento())
    assert r.status_code == 200 and r.json() == {"received": True}
    assert db.filas("wompi_payment_requests")[0]["status"] == "paid"
    efectos.confirmar.assert_called_once()
    assert efectos.notificar.call_args.kwargs["cita"] == {"id": "c1"}


def test_webhook_repetido_no_notifica_dos_veces(db, cliente_webhook):
    cliente, efectos = cliente_webhook
    db.sembrar("wompi_payment_requests", {"business_id": BIZ, "wompi_payment_link_id": "link-1", "status": "paid"})
    cliente.post(f"/wompi/webhooks/{BIZ}", json=evento())
    efectos.notificar.assert_not_called()


def test_webhook_rechazado_no_confirma(db, cliente_webhook):
    cliente, efectos = cliente_webhook
    db.sembrar("wompi_payment_requests", {"business_id": BIZ, "wompi_payment_link_id": "link-1", "status": "pending"})
    cliente.post(f"/wompi/webhooks/{BIZ}", json=evento(status="DECLINED"))
    assert db.filas("wompi_payment_requests")[0]["wompi_transaction_status"] == "DECLINED"
    efectos.confirmar.assert_not_called()


def test_webhook_firma_invalida_o_payload_roto(db, cliente_webhook):
    cliente, _ = cliente_webhook
    assert cliente.post(f"/wompi/webhooks/{BIZ}", json=evento(events_key="falsa")).status_code == 401
    assert cliente.post(f"/wompi/webhooks/{BIZ}", content=b"no-es-json").status_code == 400


def test_webhook_casos_que_se_ignoran(db, cliente_webhook):
    cliente, efectos = cliente_webhook
    assert cliente.post("/wompi/webhooks/negocio-sin-wompi", json=evento()).json() == {"received": True}
    assert cliente.post(f"/wompi/webhooks/{BIZ}", json=evento(evento_tipo="nequi_token.updated")).status_code == 200
    assert cliente.post(f"/wompi/webhooks/{BIZ}", json=evento(link=None)).status_code == 200
    assert cliente.post(f"/wompi/webhooks/{BIZ}", json=evento(link="link-de-otro")).status_code == 200
    efectos.confirmar.assert_not_called()
