"""
Dobles de prueba reutilizables: un Supabase en memoria (la API fluida de
supabase-py que usa el codigo: table().select().eq()...execute()) y un
modelo de chat con respuestas guionadas.

El Supabase falso filtra de verdad (no es un MagicMock que devuelve lo que
se le diga): asi las pruebas de services/db.py y de las rutas validan las
consultas reales que arma el codigo, no solo que "se llamo a algo".
"""

import fnmatch
import uuid
from datetime import datetime
from types import SimpleNamespace

import httpx
from postgrest.exceptions import APIError


class _Resultado:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _parsear_columnas(columnas: str):
    """
    "id, name, services(name)" -> {"id", "name", "services"}; "*" en
    cualquier posicion -> None (todas). Los joins (tabla(...)) se conservan
    como la clave anidada que las pruebas siembran en la fila.
    """
    nombres, nivel, actual = set(), 0, ""
    for caracter in columnas + ",":
        if caracter == "(":
            nivel += 1
        elif caracter == ")":
            nivel -= 1
        elif caracter == "," and nivel == 0:
            nombre = actual.split("(")[0].strip()
            if nombre == "*":
                return None
            if nombre:
                nombres.add(nombre)
            actual = ""
            continue
        actual += caracter
    return nombres or None


def _proyectar(fila: dict, columnas):
    return dict(fila) if columnas is None else {k: v for k, v in fila.items() if k in columnas}


class _Consulta:
    def __init__(self, db: "FakeSupabase", tabla: str):
        self._db = db
        self._tabla = tabla
        self._operacion = "select"
        self._payload = None
        self._on_conflict = None
        self._filtros = []
        self._orden = []
        self._limite = None
        self._rango = None
        self._contar = False
        self._columnas = None  # None = todas

    # --- operaciones -----------------------------------------------------
    def select(self, columnas="*", count=None):
        self._contar = count is not None
        self._columnas = _parsear_columnas(columnas)
        return self

    def insert(self, filas):
        self._operacion, self._payload = "insert", filas
        return self

    def update(self, valores):
        self._operacion, self._payload = "update", valores
        return self

    def upsert(self, filas, on_conflict=None):
        self._operacion, self._payload, self._on_conflict = "upsert", filas, on_conflict
        return self

    def delete(self):
        self._operacion = "delete"
        return self

    # --- filtros -----------------------------------------------------------
    def _filtro(self, funcion):
        self._filtros.append(funcion)
        return self

    def eq(self, columna, valor):
        return self._filtro(lambda f: f.get(columna) == valor)

    def neq(self, columna, valor):
        return self._filtro(lambda f: f.get(columna) != valor)

    def in_(self, columna, valores):
        valores = list(valores)
        return self._filtro(lambda f: f.get(columna) in valores)

    def gte(self, columna, valor):
        return self._filtro(lambda f: f.get(columna) is not None and f.get(columna) >= valor)

    def lte(self, columna, valor):
        return self._filtro(lambda f: f.get(columna) is not None and f.get(columna) <= valor)

    def gt(self, columna, valor):
        return self._filtro(lambda f: f.get(columna) is not None and f.get(columna) > valor)

    def lt(self, columna, valor):
        return self._filtro(lambda f: f.get(columna) is not None and f.get(columna) < valor)

    def is_(self, columna, valor):
        esperado = None if valor in (None, "null") else valor
        return self._filtro(lambda f: f.get(columna) is esperado or f.get(columna) == esperado)

    def ilike(self, columna, patron):
        patron_glob = patron.lower().replace("%", "*").replace("_", "?")
        return self._filtro(lambda f: fnmatch.fnmatch(str(f.get(columna) or "").lower(), patron_glob))

    def order(self, columna, desc=False):
        self._orden.append((columna, desc))
        return self

    def limit(self, n):
        self._limite = n
        return self

    def range(self, desde, hasta):
        self._rango = (desde, hasta)
        return self

    # --- ejecucion ---------------------------------------------------------
    def _coinciden(self, filas):
        return [f for f in filas if all(filtro(f) for filtro in self._filtros)]

    def execute(self):
        self._db.consultas.append((self._tabla, self._operacion))
        if self._db.fallar_en.get(self._tabla):
            raise self._db.fallar_en[self._tabla]
        filas = self._db.tablas.setdefault(self._tabla, [])

        if self._operacion == "select":
            resultado = [_proyectar(f, self._columnas) for f in self._coinciden(filas)]
            for columna, desc in reversed(self._orden):
                resultado.sort(key=lambda f: (f.get(columna) is None, f.get(columna)), reverse=desc)
            total = len(resultado)
            if self._rango:
                resultado = resultado[self._rango[0] : self._rango[1] + 1]
            if self._limite is not None:
                resultado = resultado[: self._limite]
            return _Resultado(resultado, total if self._contar else None)

        if self._operacion == "insert":
            nuevas = self._payload if isinstance(self._payload, list) else [self._payload]
            insertadas = [self._db.insertar(self._tabla, dict(f)) for f in nuevas]
            return _Resultado([dict(f) for f in insertadas])

        if self._operacion == "update":
            actualizadas = []
            for fila in self._coinciden(filas):
                propuesta = {**fila, **self._payload}
                self._db.validar_unicos(self._tabla, propuesta, ignorar=fila)
                fila.update(self._payload)
                actualizadas.append(dict(fila))
            return _Resultado(actualizadas)

        if self._operacion == "upsert":
            nuevas = self._payload if isinstance(self._payload, list) else [self._payload]
            conflicto = self._on_conflict or self._db.CLAVES_PRIMARIAS.get(self._tabla, "id")
            claves = [c.strip() for c in conflicto.split(",")]
            resultado = []
            for nueva in nuevas:
                existente = next(
                    (f for f in filas if all(c in nueva and f.get(c) == nueva.get(c) for c in claves)), None
                )
                if existente:
                    existente.update(nueva)
                    resultado.append(dict(existente))
                else:
                    resultado.append(dict(self._db.insertar(self._tabla, dict(nueva))))
            return _Resultado(resultado)

        if self._operacion == "delete":
            borradas = self._coinciden(filas)
            self._db.tablas[self._tabla] = [f for f in filas if f not in borradas]
            return _Resultado([dict(f) for f in borradas])

        raise AssertionError(f"operacion no soportada: {self._operacion}")


class _FakeAuthAdmin:
    def __init__(self):
        self.usuarios = []

    def list_users(self, page=1, per_page=50):
        inicio = (page - 1) * per_page
        return self.usuarios[inicio : inicio + per_page]

    def create_user(self, atributos):
        usuario = SimpleNamespace(id=str(uuid.uuid4()), email=atributos.get("email"))
        self.usuarios.append(usuario)
        return SimpleNamespace(user=usuario)


class _FakeAuth:
    def __init__(self):
        self.usuarios_por_token: dict[str, str] = {}
        self.admin = _FakeAuthAdmin()

    def get_user(self, token):
        if token not in self.usuarios_por_token:
            raise Exception("invalid JWT")
        return SimpleNamespace(user=SimpleNamespace(id=self.usuarios_por_token[token]))


class FakeSupabase:
    """Cliente de Supabase en memoria. `tablas` es {nombre: [filas]}."""

    # Tablas cuya clave primaria no es "id" (ver los .sql del repo): un upsert
    # sin on_conflict usa la clave primaria, igual que PostgREST.
    CLAVES_PRIMARIAS = {"super_admins": "user_id"}

    def __init__(self):
        # services/db.py reemplaza la sesion HTTP de postgrest al importarse
        # (_endurecer_cliente_postgrest): crear un httpx.Client no abre
        # conexiones, asi que es seguro.
        self.postgrest = SimpleNamespace(session=httpx.Client(base_url="http://supabase.test"))
        self.reset()

    def reset(self):
        self.tablas: dict[str, list[dict]] = {}
        self.unicos: list[tuple[str, tuple[str, ...], object]] = []
        self.consultas: list[tuple[str, str]] = []
        self.fallar_en: dict[str, Exception] = {}
        self.auth = _FakeAuth()

    def table(self, nombre):
        return _Consulta(self, nombre)

    # --- helpers para las pruebas -------------------------------------------
    def sembrar(self, tabla, *filas):
        """Inserta filas (completando id/created_at) y devuelve la ultima, o todas si son varias."""
        insertadas = [self.insertar(tabla, dict(f)) for f in filas]
        return insertadas[0] if len(insertadas) == 1 else insertadas

    def filas(self, tabla):
        return self.tablas.get(tabla, [])

    def agregar_unico(self, tabla, columnas, condicion=None):
        """Restriccion unica (opcionalmente parcial, como un indice WHERE) que lanza 23505 igual que Postgres."""
        self.unicos.append((tabla, tuple(columnas), condicion))

    def validar_unicos(self, tabla, fila, ignorar=None):
        for t, columnas, condicion in self.unicos:
            if t != tabla or (condicion and not condicion(fila)):
                continue
            for otra in self.tablas.get(tabla, []):
                if otra is ignorar or (condicion and not condicion(otra)):
                    continue
                if all(otra.get(c) == fila.get(c) for c in columnas):
                    raise APIError({"code": "23505", "message": "duplicate key value violates unique constraint"})

    def insertar(self, tabla, fila):
        fila.setdefault("id", str(uuid.uuid4()))
        fila.setdefault("created_at", datetime(2026, 1, 1, 12, 0, 0).isoformat())
        self.validar_unicos(tabla, fila)
        self.tablas.setdefault(tabla, []).append(fila)
        return fila


class ModeloGuionado:
    """
    Reemplazo del ChatOpenAI ya con tools: devuelve, en orden, los AIMessage
    que se le den (o el resultado de llamar a una funcion con los mensajes,
    para respuestas que dependen de la entrada). Guarda lo que recibio.
    """

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def invoke(self, mensajes):
        self.llamadas.append(mensajes)
        if not self.respuestas:
            raise AssertionError("El modelo guionado se quedo sin respuestas")
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente(mensajes) if callable(siguiente) else siguiente
