from datetime import datetime, timedelta, timezone

import httpx
import pytest
from postgrest.exceptions import APIError

from services import db as d

BIZ = "biz-1"


# --- transporte con reintento (_TransporteConReintento) ----------------------------

class _TransporteQueFallaUnaVez(d._TransporteConReintento):
    def __init__(self, error):
        super().__init__()
        self.intentos = 0
        self.error = error

    def _base(self, request):
        self.intentos += 1
        if self.intentos == 1:
            raise self.error
        return httpx.Response(200, request=request)


@pytest.fixture
def transporte(monkeypatch):
    def construir(error):
        t = _TransporteQueFallaUnaVez(error)
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, req: self._base(req))
        return t
    return construir


def test_reintenta_una_vez_los_metodos_idempotentes(transporte):
    t = transporte(httpx.RemoteProtocolError("Server disconnected"))
    respuesta = t.handle_request(httpx.Request("GET", "http://supabase.test/rest/v1/x"))
    assert respuesta.status_code == 200 and t.intentos == 2


def test_no_reintenta_un_post_para_no_duplicar_inserts(transporte):
    t = transporte(httpx.ReadError("cerrada"))
    with pytest.raises(httpx.ReadError):
        t.handle_request(httpx.Request("POST", "http://supabase.test/rest/v1/x"))
    assert t.intentos == 1


def test_endurecer_cliente_reemplaza_la_sesion_con_timeout_corto():
    from types import SimpleNamespace

    anterior = httpx.Client(base_url="http://supabase.test", headers={"apikey": "k"})
    cliente = SimpleNamespace(postgrest=SimpleNamespace(session=anterior))
    d._endurecer_cliente_postgrest(cliente)
    nueva = cliente.postgrest.session
    assert nueva is not anterior and anterior.is_closed
    assert nueva.timeout.read == 20.0 and nueva.headers["apikey"] == "k"
    assert isinstance(nueva._transport, d._TransporteConReintento)


# --- negocios -------------------------------------------------------------------

def test_busquedas_de_negocio(db):
    negocio = db.sembrar("businesses", {"name": "Barberia", "phone_number": "573001112233", "whatsapp_phone_number_id": "pn-1"})
    assert d.get_business_by_phone("573001112233")["id"] == negocio["id"]
    assert d.get_business_by_id(negocio["id"])["name"] == "Barberia"
    assert d.get_business_by_whatsapp_phone_id("pn-1")["id"] == negocio["id"]
    assert d.get_business_by_phone("x") is None
    assert d.get_business_by_id("x") is None
    assert d.get_business_by_whatsapp_phone_id("x") is None


# --- servicios y zonas -------------------------------------------------------------

def test_get_services_solo_activos_del_negocio(db):
    db.sembrar("services", {"business_id": BIZ, "name": "Corte", "active": True},
               {"business_id": BIZ, "name": "Viejo", "active": False},
               {"business_id": "otro", "name": "Ajeno", "active": True})
    assert [s["name"] for s in d.get_services(BIZ)] == ["Corte"]


def test_get_home_visit_zones_ordenadas_y_filtradas(db):
    db.sembrar("home_visit_zones", {"business_id": BIZ, "name": "Urrao", "active": True},
               {"business_id": BIZ, "name": "Betulia", "active": True},
               {"business_id": BIZ, "name": "Cerrada", "active": False})
    assert [z["name"] for z in d.get_home_visit_zones(BIZ)] == ["Betulia", "Urrao"]
    assert len(d.get_home_visit_zones(BIZ, solo_activas=False)) == 3


# --- citas -------------------------------------------------------------------------

def test_get_confirmed_appointments_for_day(db):
    base = {"business_id": BIZ, "employee_id": "e1"}
    db.sembrar("appointments",
               {**base, "scheduled_at": "2026-10-10T09:00:00", "status": "confirmed"},
               {**base, "scheduled_at": "2026-10-10T23:59:59", "status": "pending_payment"},
               {**base, "scheduled_at": "2026-10-10T10:00:00", "status": "cancelled"},
               {**base, "scheduled_at": "2026-10-11T09:00:00", "status": "confirmed"},
               {**base, "employee_id": "e2", "scheduled_at": "2026-10-10T11:00:00", "status": "confirmed"})
    assert len(d.get_confirmed_appointments_for_day(BIZ, "2026-10-10")) == 3
    assert len(d.get_confirmed_appointments_for_day(BIZ, "2026-10-10", "e1")) == 2


def test_create_appointment_en_local(db):
    [cita] = d.create_appointment(BIZ, "573001112233", "Ana", "s1", "2026-10-10T09:00:00", "e1")
    assert cita["status"] == "confirmed" and "is_home_visit" not in cita and "address" not in cita


def test_create_appointment_a_domicilio_con_zona(db):
    [cita] = d.create_appointment(BIZ, "573", "Ana", "s1", "2026-10-10T09:00:00", "e1", address="Calle 1",
                                  home_visit_zone="Betulia", home_visit_fee=5000, status="pending_payment")
    assert cita["is_home_visit"] is True and cita["address"] == "Calle 1"
    assert cita["home_visit_zone"] == "Betulia" and cita["home_visit_fee"] == 5000
    assert cita["status"] == "pending_payment"


def test_create_appointment_a_domicilio_sin_zona_no_manda_recargo(db):
    [cita] = d.create_appointment(BIZ, "573", "Ana", "s1", "2026-10-10T09:00:00", "e1", address="Calle 1")
    assert "home_visit_zone" not in cita and "home_visit_fee" not in cita


def test_create_appointment_propaga_el_choque_del_indice_unico(db):
    db.agregar_unico("appointments", ["employee_id", "scheduled_at"], lambda f: f["status"] in ("confirmed", "pending_payment"))
    d.create_appointment(BIZ, "573", "Ana", "s1", "2026-10-10T09:00:00", "e1")
    with pytest.raises(APIError) as error:
        d.create_appointment(BIZ, "574", "Luis", "s1", "2026-10-10T09:00:00", "e1")
    assert error.value.code == "23505"


def test_update_appointment_status(db):
    cita = db.sembrar("appointments", {"status": "pending_payment"})
    assert d.update_appointment_status(cita["id"], "confirmed")["status"] == "confirmed"
    assert d.update_appointment_status("no-existe", "confirmed") is None


def test_cancel_appointment_solo_si_es_del_cliente(db):
    cita = db.sembrar("appointments", {"client_phone": "573001", "status": "confirmed"})
    assert d.cancel_appointment(cita["id"], "573999") == {"error": "No se encontro esa cita para este cliente, no se cancelo nada."}
    assert db.filas("appointments")[0]["status"] == "confirmed"
    assert d.cancel_appointment(cita["id"], "573001")[0]["status"] == "cancelled"


def test_get_client_appointments_solo_confirmadas_del_cliente(db):
    db.sembrar("appointments",
               {"business_id": BIZ, "client_phone": "573001", "status": "confirmed"},
               {"business_id": BIZ, "client_phone": "573001", "status": "cancelled"},
               {"business_id": BIZ, "client_phone": "573002", "status": "confirmed"},
               {"business_id": "otro", "client_phone": "573001", "status": "confirmed"})
    assert len(d.get_client_appointments(BIZ, "573001")) == 1


def test_get_appointment_by_id_and_phone_y_full(db):
    cita = db.sembrar("appointments", {"client_phone": "573001"})
    assert d.get_appointment_by_id_and_phone(cita["id"], "573001")["id"] == cita["id"]
    assert d.get_appointment_by_id_and_phone(cita["id"], "otro") is None
    assert d.get_appointment_full(cita["id"])["id"] == cita["id"]
    assert d.get_appointment_full("x") is None


def test_update_appointment_schedule_sin_y_con_cambio_de_modalidad(db):
    cita = db.sembrar("appointments", {"scheduled_at": "2026-10-10T09:00:00", "is_home_visit": False})
    d.update_appointment_schedule(cita["id"], "2026-10-10T10:00:00")
    fila = db.filas("appointments")[0]
    assert fila["scheduled_at"] == "2026-10-10T10:00:00" and fila["is_home_visit"] is False and "address" not in fila

    d.update_appointment_schedule(cita["id"], "2026-10-10T11:00:00", is_home_visit=True, address="Calle 1",
                                  home_visit_zone="Betulia", home_visit_fee=None)
    assert fila["is_home_visit"] is True and fila["address"] == "Calle 1" and fila["home_visit_fee"] == 0


# --- chats excluidos ------------------------------------------------------------------

def test_chats_excluidos_ciclo_completo(db):
    assert d.esta_chat_excluido(BIZ, "573001") is False
    d.agregar_chat_excluido(BIZ, "573001")
    d.agregar_chat_excluido(BIZ, "573001")  # upsert: no duplica
    assert d.esta_chat_excluido(BIZ, "573001") is True
    assert len(d.listar_chats_excluidos(BIZ)) == 1
    d.eliminar_chat_excluido(BIZ, "573001")
    assert d.esta_chat_excluido(BIZ, "573001") is False


# --- empleados y horarios -----------------------------------------------------------------

def test_get_employees_y_por_id(db):
    db.sembrar("employees", {"business_id": BIZ, "name": "B", "active": True, "created_at": "2026-02-01"},
               {"business_id": BIZ, "name": "A", "active": True, "created_at": "2026-01-01"},
               {"business_id": BIZ, "name": "Inactivo", "active": False, "created_at": "2026-03-01"})
    assert [e["name"] for e in d.get_employees(BIZ)] == ["A", "B"]
    assert len(d.get_employees(BIZ, solo_activos=False)) == 3
    primero = d.get_employees(BIZ)[0]
    assert d.get_employee_by_id(primero["id"])["name"] == "A"
    assert d.get_employee_by_id("x") is None


def test_crear_empleado_normal_siembra_horario_por_defecto(db):
    empleado = d.create_employee(BIZ, "user-1", "Luis", "staff")
    horario = {f["day"]: f for f in db.filas("employee_hours") if f["employee_id"] == empleado["id"]}
    assert set(horario) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert horario["sat"]["opening_time"] == "09:00" and horario["sun"]["is_open"] is False


def test_crear_empleado_principal_hereda_el_horario_del_negocio(db):
    db.sembrar("business_hours", {"business_id": BIZ, "day": "sat", "is_open": True, "opening_time": "07:00:00",
                                  "closing_time": "20:00:00", "lunch_start": "12:30:00", "lunch_end": "13:00:00"})
    owner = d.create_employee(BIZ, "user-1", None, "owner")
    horario = {f["day"]: f for f in db.filas("employee_hours") if f["employee_id"] == owner["id"]}
    assert horario["sat"]["opening_time"] == "07:00:00" and horario["sat"]["closing_time"] == "20:00:00"
    # Dias sin fila en business_hours se heredan cerrados.
    assert horario["mon"]["is_open"] is False and horario["mon"]["opening_time"] is None


def test_crear_empleado_principal_sin_horario_de_negocio_usa_el_default(db):
    owner = d.create_employee(BIZ, "user-1", None, "owner")
    sabado = next(f for f in db.filas("employee_hours") if f["employee_id"] == owner["id"] and f["day"] == "sat")
    assert sabado["opening_time"] == "09:00"


def test_crear_empleado_no_falla_si_no_se_puede_sembrar_el_horario(db, capsys):
    db.fallar_en["employee_hours"] = RuntimeError("caido")
    empleado = d.create_employee(BIZ, "user-1", "Luis", "staff")
    assert empleado["name"] == "Luis"
    assert "No se pudo crear el horario por defecto" in capsys.readouterr().out


def test_sincronizar_horario_solo_algunos_dias_y_sin_owner(db):
    assert d.sincronizar_horario_empleado_principal(BIZ) is False  # sin owner
    owner = db.sembrar("employees", {"business_id": BIZ, "role": "owner"})
    assert d.sincronizar_horario_empleado_principal(BIZ) is False  # sin business_hours
    db.sembrar("business_hours", {"business_id": BIZ, "day": "mon", "is_open": True, "opening_time": "08:00:00",
                                  "closing_time": "18:00:00", "lunch_start": None, "lunch_end": None})
    db.sembrar("employee_hours", {"employee_id": owner["id"], "day": "tue", "is_open": True, "opening_time": "09:00"})
    assert d.sincronizar_horario_empleado_principal(BIZ, dias=["mon"]) is True
    por_dia = {f["day"]: f for f in db.filas("employee_hours")}
    assert por_dia["mon"]["opening_time"] == "08:00:00"
    assert por_dia["tue"]["opening_time"] == "09:00"  # no se toco: no estaba en `dias`


def test_get_owner_employee(db):
    assert d.get_owner_employee(BIZ) is None
    owner = db.sembrar("employees", {"business_id": BIZ, "role": "owner"})
    assert d.get_owner_employee(BIZ)["id"] == owner["id"]


def test_update_employee_y_horarios(db):
    emp = db.sembrar("employees", {"name": "A"})
    assert d.update_employee(emp["id"], {"name": "B"})["name"] == "B"
    assert d.update_employee("x", {"name": "B"}) is None
    d.upsert_employee_hours(emp["id"], "mon", {"is_open": True})
    d.upsert_employee_hours(emp["id"], "mon", {"is_open": False})
    assert len(d.get_employee_hours(emp["id"])) == 1
    assert d.get_employee_hours_for_day(emp["id"], "mon")["is_open"] is False


def test_servicios_del_empleado(db):
    db.sembrar("employee_services",
               {"employee_id": "e1", "service_id": "s1", "services": {"id": "s1", "name": "Corte", "active": True}},
               {"employee_id": "e1", "service_id": "s2", "services": {"id": "s2", "name": "Viejo", "active": False}},
               {"employee_id": "e1", "service_id": "s3", "services": None})
    assert [s["name"] for s in d.get_employee_services("e1")] == ["Corte"]

    d.set_employee_services("e1", ["s4", "s5"])
    assert sorted(f["service_id"] for f in db.filas("employee_services")) == ["s4", "s5"]
    d.set_employee_services("e1", [])
    assert db.filas("employee_services") == []


def test_asignar_servicio_a_empleado_principal(db):
    d.asignar_servicio_a_empleado_principal(BIZ, "s1")  # sin owner: no hace nada
    assert db.filas("employee_services") == []
    owner = db.sembrar("employees", {"business_id": BIZ, "role": "owner"})
    d.asignar_servicio_a_empleado_principal(BIZ, "s1")
    d.asignar_servicio_a_empleado_principal(BIZ, "s1")  # idempotente
    assert db.filas("employee_services") == [{"employee_id": owner["id"], "service_id": "s1", "id": db.filas("employee_services")[0]["id"],
                                              "created_at": db.filas("employee_services")[0]["created_at"]}]


def test_asignar_servicio_no_tumba_la_creacion_si_falla(db, capsys):
    db.fallar_en["employees"] = RuntimeError("caido")
    d.asignar_servicio_a_empleado_principal(BIZ, "s1")
    assert "No se pudo asignar el servicio" in capsys.readouterr().out


# --- ventana de 24h de WhatsApp -------------------------------------------------------------

def test_ventana_de_24_horas(db):
    assert d.cliente_dentro_de_ventana_24h(BIZ, "573001") is False
    d.registrar_mensaje_entrante_whatsapp(BIZ, "573001")
    assert d.cliente_dentro_de_ventana_24h(BIZ, "573001") is True
    db.filas("whatsapp_client_contacts")[0]["last_inbound_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert d.cliente_dentro_de_ventana_24h(BIZ, "573001") is False
    db.filas("whatsapp_client_contacts")[0]["last_inbound_at"] = None
    assert d.cliente_dentro_de_ventana_24h(BIZ, "573001") is False
