from datetime import datetime

import pytest

from services import scheduling as s

EMP = "emp-1"
BIZ = "biz-1"
AHORA = datetime(2026, 10, 7, 8, 0)  # miercoles 7 de octubre, 8:00 am
SABADO = "2026-10-10"


@pytest.fixture(autouse=True)
def hora_fija(monkeypatch):
    monkeypatch.setattr(s, "ahora_local", lambda: AHORA)


def _horario(db, dia="sat", **campos):
    fila = {"employee_id": EMP, "day": dia, "is_open": True, "opening_time": "09:00:00", "closing_time": "12:00:00",
            "lunch_start": None, "lunch_end": None}
    fila.update(campos)
    return db.sembrar("employee_hours", fila)


def _cita(db, hora, duracion=30, status="confirmed", empleado=EMP, cita_id=None):
    fila = {"business_id": BIZ, "employee_id": empleado, "scheduled_at": f"{SABADO}T{hora}:00", "status": status,
            "services": {"duration_minutes": duracion}}
    if cita_id:
        fila["id"] = cita_id
    return db.sembrar("appointments", fila)


# --- ahora_local / formatear_fecha_natural -------------------------------------

def test_ahora_local_es_naive_en_hora_de_colombia(monkeypatch):
    monkeypatch.undo()
    valor = s.ahora_local()
    assert valor.tzinfo is None
    utc = datetime.utcnow()
    diferencia_horas = round((utc - valor).total_seconds() / 3600)
    assert diferencia_horas == 5  # Colombia no tiene horario de verano: siempre UTC-5


@pytest.mark.parametrize(
    "fecha, esperado",
    [
        (datetime(2026, 10, 7, 15, 0), "hoy a las 3:00 pm"),
        (datetime(2026, 10, 8, 9, 30), "mañana a las 9:30 am"),
        (datetime(2026, 10, 10, 12, 0), "sabado 10 de octubre a las 12:00 pm"),
        (datetime(2027, 1, 4, 7, 5), "lunes 4 de enero a las 7:05 am"),
    ],
)
def test_formatear_fecha_natural(fecha, esperado):
    assert s.formatear_fecha_natural(fecha) == esperado


# --- generar_horas_disponibles -------------------------------------------------

def test_dia_sin_horario_o_cerrado_no_tiene_horas(db):
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO) == []
    _horario(db, is_open=False)
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO) == []


def test_turnos_cada_30_minutos_dentro_del_horario(db):
    _horario(db)
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO) == ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30"]


def test_el_ultimo_turno_debe_terminar_antes_del_cierre(db):
    _horario(db)
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO, duracion_minutos=45)[-1] == "11:00"


def test_excluye_almuerzo_comparando_el_rango_completo(db):
    _horario(db, lunch_start="10:30:00", lunch_end="11:00:00")
    # 10:00 con 45 min terminaria 10:45, dentro del almuerzo: tampoco se ofrece.
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO, duracion_minutos=45) == ["09:00", "09:30", "11:00"]


def test_excluye_citas_existentes_segun_su_duracion(db):
    _horario(db)
    _cita(db, "09:30", duracion=35)  # ocupa 9:30-10:05, tapa tambien el turno de las 10:00
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO) == ["09:00", "10:30", "11:00", "11:30"]


def test_pending_payment_bloquea_el_cupo_pero_cancelada_no(db):
    _horario(db)
    _cita(db, "09:00", status="pending_payment")
    _cita(db, "10:00", status="cancelled")
    assert "09:00" not in s.generar_horas_disponibles(BIZ, EMP, SABADO)
    assert "10:00" in s.generar_horas_disponibles(BIZ, EMP, SABADO)


def test_citas_de_otro_empleado_no_bloquean(db):
    _horario(db)
    _cita(db, "09:00", empleado="otro-empleado")
    assert "09:00" in s.generar_horas_disponibles(BIZ, EMP, SABADO)


def test_cita_sin_servicio_cuenta_30_minutos(db):
    _horario(db)
    db.sembrar("appointments", {"business_id": BIZ, "employee_id": EMP, "scheduled_at": f"{SABADO}T09:00:00",
                                "status": "confirmed", "services": None})
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO)[:2] == ["09:30", "10:00"]


def test_hoy_no_ofrece_horas_que_ya_pasaron(db, monkeypatch):
    monkeypatch.setattr(s, "ahora_local", lambda: datetime(2026, 10, 10, 10, 15))
    _horario(db)
    assert s.generar_horas_disponibles(BIZ, EMP, SABADO) == ["10:30", "11:00", "11:30"]


# --- es_hora_valida --------------------------------------------------------------

def test_hora_valida(db):
    _horario(db)
    assert s.es_hora_valida(f"{SABADO}T10:00:00", EMP) == (True, "")


@pytest.mark.parametrize(
    "fecha_hora, duracion, fragmento",
    [
        ("no-es-fecha", 30, "formato"),
        ("2026-10-06T10:00:00", 30, "ya pasaron"),
        (f"{SABADO}T08:30:00", 30, "fuera del horario"),
        (f"{SABADO}T11:45:00", 30, "fuera del horario"),
    ],
)
def test_hora_invalida(db, fecha_hora, duracion, fragmento):
    _horario(db)
    valida, mensaje = s.es_hora_valida(fecha_hora, EMP, duracion)
    assert not valida and fragmento in mensaje


def test_hora_valida_rechaza_dia_cerrado(db):
    _horario(db, is_open=False)
    valida, mensaje = s.es_hora_valida(f"{SABADO}T10:00:00", EMP)
    assert not valida and mensaje == "No atiende los sabado."


def test_hora_valida_rechaza_cita_que_termina_dentro_del_almuerzo(db):
    _horario(db, lunch_start="10:30:00", lunch_end="11:00:00")
    valida, mensaje = s.es_hora_valida(f"{SABADO}T10:00:00", EMP, 45)
    assert not valida and "almuerzo (10:30 a 11:00)" in mensaje


# --- hay_choque_de_horario ---------------------------------------------------------

def test_detecta_choque_parcial(db):
    _cita(db, "09:30", duracion=35)
    choque, mensaje = s.hay_choque_de_horario(BIZ, EMP, datetime(2026, 10, 10, 10, 0), 30)
    assert choque and mensaje == "Ya hay una cita agendada de 09:30 a 10:05. Por favor elige otra hora."


def test_citas_contiguas_no_chocan(db):
    _cita(db, "09:30")
    assert s.hay_choque_de_horario(BIZ, EMP, datetime(2026, 10, 10, 10, 0), 30) == (False, "")
    assert s.hay_choque_de_horario(BIZ, EMP, datetime(2026, 10, 10, 9, 0), 30) == (False, "")


def test_reprogramar_ignora_la_propia_cita(db):
    _cita(db, "09:30", cita_id="la-misma")
    assert s.hay_choque_de_horario(BIZ, EMP, datetime(2026, 10, 10, 9, 45), 30, ignorar_appointment_id="la-misma") == (False, "")
    assert s.hay_choque_de_horario(BIZ, EMP, datetime(2026, 10, 10, 9, 45), 30)[0] is True


def test_obtener_horario_dia_delega_en_la_base(db):
    fila = _horario(db, dia="mon")
    assert s.obtener_horario_dia(EMP, "mon")["id"] == fila["id"]
    assert s.obtener_horario_dia(EMP, "tue") is None
