"""Verifica que la infraestructura de pruebas aisla de verdad (nada real)."""
import os
import socket

import pytest

from tests.conftest import RedBloqueadaEnPruebas


def test_variables_de_entorno_son_falsas():
    assert os.environ["SUPABASE_URL"] == "http://supabase.test"
    assert os.environ["OPENAI_API_KEY"] == "sk-test"


def test_red_bloqueada():
    with pytest.raises(RedBloqueadaEnPruebas):
        socket.create_connection(("api.goagenda.online", 443), timeout=1)


def test_supabase_es_el_doble_en_memoria(db):
    from services import db as servicios_db

    assert servicios_db.supabase is db


def test_importar_main_no_tiene_efectos_reales(db):
    import main

    assert type(main.scheduler).__name__ == "_SchedulerFalso"
    assert all(nombre != "auth.admin" for nombre, _ in db.consultas)


def test_checkpointer_del_agente_en_memoria():
    from agent import graph

    assert type(graph._checkpointer).__name__ == "_CheckpointerEnMemoria"
