"""Servicios de soporte: WhatsApp, push, tiempo real, uso de IA, super admin, Baileys, codigos de invitacion, geocodificacion, recordatorios y auth."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import requests
from fastapi import HTTPException
from postgrest.exceptions import APIError

BIZ = "biz-1"


class RespuestaHttp:
    def __init__(self, status=200, data=None, texto=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = texto if texto is not None else json.dumps(self._data)

    def json(self):
        return self._data


# ------------------------------------------------------------------------------
# WhatsApp (Meta Cloud API)
# ------------------------------------------------------------------------------

from services import whatsapp as wa  # noqa: E402


@pytest.mark.parametrize(
    "numero, normalizado",
    [("3001234567", "573001234567"), ("+57 300 123 4567", "573001234567"), ("573001234567", "573001234567"),
     ("6041234567", None), ("12345", None), ("", None)],
)
def test_normalizar_numero_whatsapp(numero, normalizado):
    assert wa.normalizar_numero_whatsapp(numero) == normalizado


def test_mensaje_contacto_humano():
    assert wa.construir_mensaje_contacto_humano({"phone_number": "300 123 4567"}).endswith("https://wa.me/573001234567")
    assert "Contacta directamente" in wa.construir_mensaje_contacto_humano({"phone_number": "fijo"})
    assert "Contacta directamente" in wa.construir_mensaje_contacto_humano(None)


@pytest.fixture
def meta(monkeypatch):
    llamadas = []
    estado = SimpleNamespace(respuesta=RespuestaHttp(200, {"messages": [{"id": "wamid"}]}), error=None)

    def post(url, **kwargs):
        llamadas.append((url, kwargs))
        if estado.error:
            raise estado.error
        return estado.respuesta

    monkeypatch.setattr(wa.requests, "post", post)
    estado.llamadas = llamadas
    return estado


def test_enviar_texto_libre(meta):
    assert wa.send_whatsapp_message("573001", "hola", "pn-1") == {"messages": [{"id": "wamid"}]}
    url, kwargs = meta.llamadas[0]
    assert url.endswith("/pn-1/messages") and kwargs["json"]["text"] == {"body": "hola"}
    assert kwargs["headers"]["Authorization"] == "Bearer wa-test-token"


def test_enviar_texto_libre_errores(meta):
    meta.respuesta = RespuestaHttp(400, {"error": {"message": "fuera de la ventana de 24h"}})
    assert "error" in wa.send_whatsapp_message("573", "hola", "pn")
    meta.error = requests.exceptions.ConnectionError("sin red")
    assert wa.send_whatsapp_message("573", "hola", "pn") == {"error": "sin red"}


def test_confirmacion_y_recordatorio_usan_plantilla(meta):
    wa.enviar_confirmacion_cita_cliente("573001", "Ana", "Barberia", "Corte", "mañana a las 9:00 am")
    template = meta.llamadas[0][1]["json"]["template"]
    assert template["name"] == "confirmacion_cita_goagenda"
    assert [p["text"] for p in template["components"][0]["parameters"]] == ["Ana", "Barberia", "Corte", "mañana a las 9:00 am"]
    wa.enviar_recordatorio_cita_template("573001", "Ana", "Barberia", "Corte", "hoy")
    assert meta.llamadas[1][1]["json"]["template"]["name"] == "recordatorio_cita_goagenda"


def test_plantilla_errores(meta):
    meta.respuesta = RespuestaHttp(400, {"error": "template rechazado"})
    assert wa.enviar_confirmacion_cita_cliente("573", "A", "B", "C", "D") == {"error": {"error": "template rechazado"}}
    meta.error = requests.exceptions.Timeout("lento")
    assert wa.enviar_recordatorio_cita_template("573", "A", "B", "C", "D") == {"error": "lento"}


def test_sin_credenciales_de_goagenda_no_envia(monkeypatch, meta):
    monkeypatch.setattr(wa, "GOAGENDA_WHATSAPP_TOKEN", None)
    assert "no configuradas" in wa.enviar_confirmacion_cita_cliente("573", "A", "B", "C", "D")["error"]
    assert "no configuradas" in wa.enviar_recordatorio_cita_template("573", "A", "B", "C", "D")["error"]
    assert meta.llamadas == []


# ------------------------------------------------------------------------------
# Notificaciones push (Firebase)
# ------------------------------------------------------------------------------

from services import push_notifications as push  # noqa: E402


@pytest.fixture
def firebase(monkeypatch):
    estado = SimpleNamespace(enviados=[], certificados=[])
    monkeypatch.setattr(push, "_firebase_app", None)
    monkeypatch.setattr(push.credentials, "Certificate", lambda origen: estado.certificados.append(origen) or "cred")
    monkeypatch.setattr(push.firebase_admin, "initialize_app", lambda cred: "app")
    monkeypatch.setattr(push.messaging, "send", lambda mensaje: estado.enviados.append(mensaje) or "msg-id")
    return estado


def test_inicializa_firebase_desde_variable_de_entorno_una_sola_vez(firebase, monkeypatch):
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", json.dumps({"type": "service_account"}))
    assert push._inicializar_firebase() == "app"
    push._inicializar_firebase()
    assert firebase.certificados == [{"type": "service_account"}]


def test_inicializa_firebase_desde_archivo(firebase, monkeypatch):
    monkeypatch.delenv("FIREBASE_CREDENTIALS_JSON", raising=False)
    push._inicializar_firebase()
    assert firebase.certificados[0].endswith("firebase-credentials.json")


def test_notificaciones_push(firebase, monkeypatch, capsys):
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "{}")
    push.enviar_notificacion_nueva_cita("tok", "Ana", "Corte", "hoy")
    push.enviar_notificacion_cita_cancelada("tok", "Ana", "Corte", "hoy")
    push.enviar_notificacion_cita_reprogramada("tok", "Ana", "Corte", "hoy", "mañana")
    push.enviar_notificacion_pago_recibido("tok", "$7.500", "Abono")
    push.enviar_notificacion_escalamiento("tok", "Ana", BIZ, "sess-1")
    titulos = [m.notification.title for m in firebase.enviados]
    assert titulos == ["Nueva cita agendada", "Cita cancelada", "Cita reprogramada", "Pago recibido", "Un cliente necesita ayuda"]
    assert firebase.enviados[-1].data == {"tipo": "chat_escalado", "business_id": BIZ, "session_id": "sess-1"}
    assert firebase.enviados[0].token == "tok"


def test_push_sin_token_o_con_error(firebase, monkeypatch, capsys):
    push.enviar_notificacion_nueva_cita(None, "Ana", "Corte", "hoy")
    assert firebase.enviados == [] and "No hay fcm_token" in capsys.readouterr().out
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "{}")

    def falla(mensaje):
        raise RuntimeError("token invalido")

    monkeypatch.setattr(push.messaging, "send", falla)
    push.enviar_notificacion_nueva_cita("tok", "Ana", "Corte", "hoy")
    assert "Error enviando notificacion push: token invalido" in capsys.readouterr().out


# ------------------------------------------------------------------------------
# Tiempo real (WebSocket del panel)
# ------------------------------------------------------------------------------

from services import realtime  # noqa: E402


class WebSocketFalso:
    def __init__(self, falla=False):
        self.enviados, self.falla, self.aceptado = [], falla, False

    async def accept(self):
        self.aceptado = True

    async def send_text(self, texto):
        if self.falla:
            raise RuntimeError("desconectado")
        self.enviados.append(json.loads(texto))


def test_gestor_registrar_desconectar_y_difundir():
    gestor = realtime.GestorConexionesTiempoReal()
    sano, muerto = WebSocketFalso(), WebSocketFalso(falla=True)
    asyncio.run(gestor.aceptar(sano))
    assert sano.aceptado
    gestor.registrar(BIZ, sano)
    gestor.registrar(BIZ, muerto)
    asyncio.run(gestor._difundir(BIZ, {"type": "x"}))
    assert sano.enviados == [{"type": "x"}]
    assert gestor._conexiones[BIZ] == {sano}  # el socket muerto se limpia solo
    asyncio.run(gestor._difundir("sin-conexiones", {"type": "x"}))
    gestor.desconectar(BIZ, sano)
    assert BIZ not in gestor._conexiones
    gestor.desconectar("nada", sano)


def test_gestor_emitir_desde_codigo_sync():
    gestor = realtime.GestorConexionesTiempoReal()
    ws = WebSocketFalso()
    gestor.registrar(BIZ, ws)
    gestor.emitir(BIZ, {"type": "x"})  # sin loop registrado: no hace nada
    assert ws.enviados == []

    async def escenario():
        gestor.registrar_loop(asyncio.get_running_loop())
        gestor.emitir(BIZ, {"type": "appointment.created"})
        await asyncio.sleep(0.01)

    asyncio.run(escenario())
    assert ws.enviados == [{"type": "appointment.created"}]


def test_gestor_emitir_con_loop_cerrado_no_lanza():
    gestor = realtime.GestorConexionesTiempoReal()
    gestor.registrar(BIZ, WebSocketFalso())
    loop = asyncio.new_event_loop()
    loop.close()
    gestor.registrar_loop(loop)
    gestor.emitir(BIZ, {"type": "x"})


def test_emitir_evento_cita(db, monkeypatch, capsys):
    emitir = MagicMock()
    monkeypatch.setattr(realtime.gestor_tiempo_real, "emitir", emitir)
    realtime.emitir_evento_cita("appointment.created", BIZ, "no-existe")
    emitir.assert_not_called()
    cita = db.sembrar("appointments", {"business_id": BIZ})
    realtime.emitir_evento_cita("appointment.created", BIZ, cita["id"])
    assert emitir.call_args.args[1]["appointment"]["id"] == cita["id"]
    db.fallar_en["appointments"] = RuntimeError("caido")
    realtime.emitir_evento_cita("appointment.created", BIZ, cita["id"])
    assert "No se pudo emitir el evento de tiempo real" in capsys.readouterr().out


# ------------------------------------------------------------------------------
# Uso de IA y presupuesto
# ------------------------------------------------------------------------------

from services import ai_usage_tracking as uso  # noqa: E402


def test_calcular_costo():
    assert uso.calcular_costo(1_000_000, 1_000_000) == 2.0
    assert uso.calcular_costo(0, 0) == 0


def test_registrar_uso_y_alerta_de_presupuesto_una_vez_al_mes(db, monkeypatch):
    alerta = MagicMock()
    monkeypatch.setattr(uso, "enviar_notificacion_nueva_cita", alerta)
    db.sembrar("businesses", {"id": BIZ, "monthly_ai_budget_usd": 1.0, "fcm_token": "tok"})
    db.sembrar("ai_usage_log", {"business_id": BIZ, "estimated_cost_usd": 0.9, "created_at": datetime.now().isoformat()})
    assert uso.registrar_uso(BIZ, 1000, 1000, 2000) == uso.calcular_costo(1000, 1000)
    alerta.assert_called_once()
    assert db.filas("businesses")[0]["ai_budget_alert_sent_month"] == datetime.now().strftime("%Y-%m")
    uso.registrar_uso(BIZ, 10, 10, 20)
    alerta.assert_called_once()  # no se repite en el mismo mes


def test_presupuesto_sin_token_sin_negocio_y_bajo_el_limite(db, monkeypatch, capsys):
    alerta = MagicMock(side_effect=RuntimeError("firebase"))
    monkeypatch.setattr(uso, "enviar_notificacion_nueva_cita", alerta)
    uso._verificar_presupuesto("no-existe")
    db.sembrar("businesses", {"id": BIZ, "monthly_ai_budget_usd": None})
    uso._verificar_presupuesto(BIZ)  # presupuesto default de 5 USD, gasto 0: nada
    assert "ai_budget_alert_sent_month" not in db.filas("businesses")[0]
    db.sembrar("ai_usage_log", {"business_id": BIZ, "estimated_cost_usd": 4.5, "created_at": datetime.now().isoformat()})
    uso._verificar_presupuesto(BIZ)  # sin fcm_token: no avisa pero marca el mes
    alerta.assert_not_called()
    db.filas("businesses")[0].update({"fcm_token": "tok", "ai_budget_alert_sent_month": None})
    uso._verificar_presupuesto(BIZ)
    assert "Error enviando alerta de presupuesto" in capsys.readouterr().out


def test_registrar_uso_no_falla_si_no_puede_guardar(db, capsys):
    db.fallar_en["ai_usage_log"] = RuntimeError("caido")
    db.fallar_en["businesses"] = None
    uso.registrar_uso("no-existe", 1, 1, 2)
    assert "Error guardando registro de uso de IA" in capsys.readouterr().out


# ------------------------------------------------------------------------------
# Super admin por defecto
# ------------------------------------------------------------------------------

from services import seed_super_admin as seed  # noqa: E402


def test_sembrar_super_admin(db, monkeypatch, capsys):
    seed.sembrar_super_admin_por_defecto()  # sin variables: no hace nada
    assert db.filas("super_admins") == []
    monkeypatch.setenv("SUPER_ADMIN_EMAIL", "Admin@GoAgenda.test")
    monkeypatch.setenv("SUPER_ADMIN_PASSWORD", "secreto")
    seed.sembrar_super_admin_por_defecto()
    assert len(db.auth.admin.usuarios) == 1 and "Super admin creado" in capsys.readouterr().out
    seed.sembrar_super_admin_por_defecto()  # idempotente: no crea otro usuario
    assert len(db.auth.admin.usuarios) == 1 and len(db.filas("super_admins")) == 1


def test_buscar_usuario_pagina_por_pagina(db):
    db.auth.admin.usuarios = [SimpleNamespace(id=str(i), email=f"u{i}@x.co") for i in range(250)] + [SimpleNamespace(id="sin", email=None)]
    assert seed._buscar_usuario_por_email("U249@x.co").id == "249"
    assert seed._buscar_usuario_por_email("nadie@x.co") is None
    db.auth.admin.usuarios = [SimpleNamespace(id=str(i), email=f"u{i}@x.co") for i in range(200)]
    assert seed._buscar_usuario_por_email("nadie@x.co") is None


def test_sembrar_super_admin_no_tumba_el_arranque(db, monkeypatch, capsys):
    monkeypatch.setenv("SUPER_ADMIN_EMAIL", "a@b.co")
    monkeypatch.setenv("SUPER_ADMIN_PASSWORD", "x")
    db.fallar_en["super_admins"] = RuntimeError("caido")
    seed.sembrar_super_admin_por_defecto()
    assert "No se pudo sembrar el super admin" in capsys.readouterr().out


# ------------------------------------------------------------------------------
# Baileys (WhatsApp no oficial)
# ------------------------------------------------------------------------------

from services import baileys_client as baileys  # noqa: E402


def test_baileys(monkeypatch):
    monkeypatch.setattr(baileys.requests, "post", lambda url, **kw: RespuestaHttp(200, {"ok": True, "url": url}))
    monkeypatch.setattr(baileys.requests, "get", lambda url, **kw: RespuestaHttp(200, {"connected": True}))
    assert baileys.send_baileys_message(BIZ, "573", "hola")["ok"] is True
    assert baileys.solicitar_pairing_code(BIZ, "573")["url"].endswith(f"/pairing-code/{BIZ}")
    assert baileys.estado_conexion(BIZ) == {"connected": True}


def test_baileys_errores(monkeypatch):
    monkeypatch.setattr(baileys.requests, "post", lambda url, **kw: RespuestaHttp(503, {}))
    assert baileys.send_baileys_message(BIZ, "573", "hola") == {"error": "HTTP 503"}
    monkeypatch.setattr(baileys.requests, "post", lambda url, **kw: RespuestaHttp(400, {"error": "no vinculado"}))
    assert baileys.send_baileys_message(BIZ, "573", "hola") == {"error": "no vinculado"}

    def sin_red(url, **kw):
        raise requests.exceptions.ConnectionError("sin red")

    monkeypatch.setattr(baileys.requests, "post", sin_red)
    assert baileys.send_baileys_message(BIZ, "573", "hola") == {"error": "sin red"}


# ------------------------------------------------------------------------------
# Codigos de invitacion
# ------------------------------------------------------------------------------

from services import invitation_codes as codigos  # noqa: E402


def test_crear_y_usar_codigo(db):
    codigo = codigos.crear_codigo_invitacion(BIZ, "employee", "user-1", "Luis")
    assert len(codigo["code"]) == 6 and codigo["code"].isalnum() and codigo["code"].isupper()
    assert codigos.obtener_codigo_sin_usar(f"  {codigo['code'].lower()} ")["id"] == codigo["id"]
    codigos.marcar_codigo_usado(codigo["id"], "user-2")
    assert codigos.obtener_codigo_sin_usar(codigo["code"]) is None


def test_codigo_repetido_reintenta(db, monkeypatch):
    db.agregar_unico("invitation_codes", ["code"])
    secuencia = iter(["AAAAAA", "AAAAAA", "BBBBBB"])
    monkeypatch.setattr(codigos, "_generar_codigo", lambda: next(secuencia))
    codigos.crear_codigo_invitacion(BIZ, "admin", "u")
    assert codigos.crear_codigo_invitacion(BIZ, "admin", "u")["code"] == "BBBBBB"


def test_codigo_sin_suerte_o_con_otro_error(db, monkeypatch):
    db.agregar_unico("invitation_codes", ["code"])
    monkeypatch.setattr(codigos, "_generar_codigo", lambda: "AAAAAA")
    codigos.crear_codigo_invitacion(BIZ, "admin", "u")
    with pytest.raises(RuntimeError, match="unico"):
        codigos.crear_codigo_invitacion(BIZ, "admin", "u")
    db.fallar_en["invitation_codes"] = APIError({"code": "42501", "message": "permiso"})
    with pytest.raises(APIError):
        codigos.crear_codigo_invitacion(BIZ, "admin", "u")


# ------------------------------------------------------------------------------
# Geocodificacion (Nominatim)
# ------------------------------------------------------------------------------

from services import geocoding as geo  # noqa: E402


@pytest.fixture
def nominatim(monkeypatch):
    estado = SimpleNamespace(llamadas=[], respuesta=httpx.Response(200, json=[{"address": {"town": "Betulia"}}]), error=None)

    def get(url, **kwargs):
        estado.llamadas.append(kwargs)
        if estado.error:
            raise estado.error
        return estado.respuesta

    monkeypatch.setattr(geo.httpx, "get", get)
    monkeypatch.setattr(geo, "_INTERVALO_MINIMO_SEGUNDOS", 0)
    return estado


def test_geocodificar_y_cache(nominatim):
    assert geo.geocodificar("Calle 1, Betulia") == {"town": "Betulia"}
    assert geo.geocodificar("  calle 1,   BETULIA ") == {"town": "Betulia"}  # misma clave normalizada: cache
    assert len(nominatim.llamadas) == 1
    params = nominatim.llamadas[0]["params"]
    assert params["countrycodes"] == "co" and nominatim.llamadas[0]["headers"]["User-Agent"].startswith("GoAgenda")
    assert geo.geocodificar("   ") is None


@pytest.mark.parametrize("respuesta, error", [(httpx.Response(200, json=[]), None), (httpx.Response(503), None),
                                              (httpx.Response(200, text="no-json"), None), (None, httpx.ConnectTimeout("lento"))])
def test_geocodificar_nunca_lanza(nominatim, respuesta, error):
    nominatim.respuesta, nominatim.error = respuesta, error
    assert geo.geocodificar("Calle falsa 123") is None


def test_rate_limit_espera_entre_llamadas(monkeypatch):
    esperas = []
    monkeypatch.setattr(geo.time, "sleep", esperas.append)
    monkeypatch.setattr(geo, "_ultima_llamada", 1000.0)
    monkeypatch.setattr(geo.time, "monotonic", lambda: 1000.5)
    geo._esperar_rate_limit()
    assert esperas and esperas[0] == pytest.approx(geo._INTERVALO_MINIMO_SEGUNDOS - 0.5)


def test_localidades_de():
    assert geo.localidades_de({"suburb": "Laureles", "city": "Medellin", "country": "Colombia"}) == ["Laureles", "Medellin"]


# ------------------------------------------------------------------------------
# Recordatorios
# ------------------------------------------------------------------------------

from services import reminder_service as recordatorios  # noqa: E402


def test_recordatorios(db, monkeypatch, capsys):
    monkeypatch.setattr(recordatorios, "ahora_local", lambda: datetime(2026, 10, 10, 8, 0))
    from services import scheduling

    monkeypatch.setattr(scheduling, "ahora_local", lambda: datetime(2026, 10, 10, 8, 0))
    enviados = []

    def enviar(**kw):
        enviados.append(kw)
        return {"error": "fallo"} if kw["to"] == "573fallo" else {"messages": []}

    monkeypatch.setattr(recordatorios, "enviar_recordatorio_cita_template", enviar)
    db.sembrar("businesses", {"id": BIZ, "name": "Barberia", "reminder_hours_before": 3}, {"id": "otro", "name": "Otro", "reminder_hours_before": None})
    base = {"business_id": BIZ, "status": "confirmed", "reminder_sent": False, "services": {"name": "Corte"}}
    db.sembrar("appointments",
               {**base, "id": "ok", "client_phone": "573001", "client_name": "Ana", "scheduled_at": "2026-10-10T10:00:00"},
               {**base, "id": "sin-tel", "client_phone": "", "scheduled_at": "2026-10-10T10:30:00"},
               {**base, "id": "falla", "client_phone": "573fallo", "scheduled_at": "2026-10-10T09:00:00", "services": None},
               {**base, "id": "lejana", "client_phone": "573002", "scheduled_at": "2026-10-10T15:00:00"},
               {**base, "id": "ya-enviada", "client_phone": "573003", "scheduled_at": "2026-10-10T09:00:00", "reminder_sent": True})
    recordatorios.revisar_y_enviar_recordatorios()
    assert sorted(e["to"] for e in enviados) == ["573001", "573fallo"]
    estado = {f["id"]: f["reminder_sent"] for f in db.filas("appointments")}
    assert estado == {"ok": True, "sin-tel": False, "falla": False, "lejana": False, "ya-enviada": True}
    salida = capsys.readouterr().out
    assert "Fallo el envio de recordatorio para cita falla" in salida and "enviados=1" in salida


def test_recordatorios_tolera_fallas(db, monkeypatch, capsys):
    monkeypatch.setattr(recordatorios, "ahora_local", lambda: datetime(2026, 10, 10, 8, 0))
    db.sembrar("businesses", {"id": BIZ, "name": "Barberia"})
    db.sembrar("appointments", {"business_id": BIZ, "status": "confirmed", "reminder_sent": False, "client_phone": "573",
                                "scheduled_at": "2026-10-10T09:00:00"})
    monkeypatch.setattr(recordatorios, "enviar_recordatorio_cita_template", MagicMock(side_effect=RuntimeError("meta caido")))
    recordatorios.revisar_y_enviar_recordatorios()
    assert "Error enviando recordatorio" in capsys.readouterr().out
    db.fallar_en["appointments"] = RuntimeError("caido")
    recordatorios.revisar_y_enviar_recordatorios()
    assert "error consultando citas" in capsys.readouterr().out


# ------------------------------------------------------------------------------
# Autenticacion y permisos
# ------------------------------------------------------------------------------

from services import auth  # noqa: E402


def test_obtener_usuario_actual(db):
    db.auth.usuarios_por_token["tok"] = "user-1"
    assert auth.obtener_usuario_actual("Bearer tok") == "user-1"
    for header in [None, "Basic x", "Bearer otro"]:
        with pytest.raises(HTTPException) as e:
            auth.obtener_usuario_actual(header)
        assert e.value.status_code == 401


def test_usuario_vacio_es_401(db, monkeypatch):
    monkeypatch.setattr(db.auth, "get_user", lambda token: SimpleNamespace(user=None))
    with pytest.raises(HTTPException):
        auth.obtener_usuario_actual("Bearer tok")


@pytest.fixture
def negocio(db):
    db.sembrar("businesses", {"id": BIZ, "owner_id": "dueno", "blocked": False})
    db.sembrar("employees", {"id": "emp-1", "business_id": BIZ, "user_id": "empleado", "active": True},
               {"id": "emp-inactivo", "business_id": BIZ, "user_id": "exempleado", "active": False})


def codigo_http(funcion, *args):
    try:
        funcion(*args)
        return 200
    except HTTPException as e:
        return e.status_code


def test_verificar_dueno(negocio, db):
    assert codigo_http(auth.verificar_dueno, BIZ, "dueno") == 200
    assert codigo_http(auth.verificar_dueno, BIZ, "empleado") == 403
    assert codigo_http(auth.verificar_dueno, "no-existe", "dueno") == 404
    db.filas("businesses")[0]["blocked"] = True
    assert codigo_http(auth.verificar_dueno, BIZ, "dueno") == 403


def test_verificar_acceso_negocio(negocio, db):
    assert codigo_http(auth.verificar_acceso_negocio, BIZ, "dueno") == 200
    assert codigo_http(auth.verificar_acceso_negocio, BIZ, "empleado") == 200
    assert codigo_http(auth.verificar_acceso_negocio, BIZ, "exempleado") == 403
    assert codigo_http(auth.verificar_acceso_negocio, "no-existe", "dueno") == 404
    db.filas("businesses")[0]["blocked"] = True
    assert codigo_http(auth.verificar_acceso_negocio, BIZ, "dueno") == 403


def test_verificar_acceso_empleado(negocio, db):
    assert auth.verificar_acceso_empleado("emp-1", "empleado") == BIZ
    assert auth.verificar_acceso_empleado("emp-1", "dueno") == BIZ
    assert codigo_http(auth.verificar_acceso_empleado, "emp-1", "intruso") == 403
    assert codigo_http(auth.verificar_acceso_empleado, "no-existe", "dueno") == 404
    db.filas("businesses")[0]["blocked"] = True
    assert codigo_http(auth.verificar_acceso_empleado, "emp-1", "dueno") == 403


def test_super_admin(db):
    assert auth.es_super_admin("u") is False
    assert codigo_http(auth.requiere_super_admin, "u") == 403
    db.sembrar("super_admins", {"user_id": "u"})
    assert auth.requiere_super_admin("u") == "u"


def test_api_key_interna():
    assert auth.requiere_api_key_interna("internal-test-key") is None
    for clave in [None, "otra"]:
        assert codigo_http(auth.requiere_api_key_interna, clave) == 401
