# GoAgenda Backend

API en FastAPI para GoAgenda. El corazon del producto es un agente de IA (LangGraph) que agenda, consulta, cancela y reprograma citas conversando por un chat web publico y aislado por negocio: cada negocio tiene su propio enlace de chat (`/chat/<business_id>/...`) que su dueño comparte con sus clientes. WhatsApp (Meta Cloud API oficial, o un numero vinculado via un microservicio Baileys aparte) se usa solo para envios salientes: recordatorios de citas y confirmaciones automaticas de reservas hechas en la web.

Proyecto 100% Python. No requiere Node ni npm para correr.

## Requisitos

- Python 3.12+
- Un proyecto de Supabase (base de datos + auth), con acceso a su conexion Postgres directa (no solo la REST API)
- Una API key de OpenAI
- Credenciales de Firebase (para las notificaciones push), opcional en desarrollo

## Instalacion

```bash
# 1. Clonar el repo y entrar a la carpeta
git clone <url-del-repo>
cd goagenda-backend

# 2. Crear y activar un entorno virtual (Python 3.12)
python3.12 -m venv venv
source venv/bin/activate   # en Windows: venv\Scripts\activate

# 3. Instalar dependencias
python -m pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# luego edita .env y completa los valores reales
```

### DATABASE_URL (checkpointer del agente de chat)

El agente de chat usa el checkpointer `PostgresSaver` de LangGraph para persistir el contexto de cada conversacion (sobrevive reinicios del backend y funciona con varias instancias corriendo a la vez). Necesita una conexion Postgres directa, no la REST API de Supabase:

1. Supabase Dashboard → tu proyecto → **Project Settings → Database → Connection string**.
2. Usa la pestaña **Session pooler** (puerto `5432`), **no** "Direct connection" (host `db.<project-ref>.supabase.co`, IPv6-only y suele no resolver desde redes normales) ni "Transaction pooler" (puerto `6543`, rompe el manejo de sesiones que necesita el checkpointer).
3. El host se ve como `aws-0-<region>.pooler.supabase.com` y el usuario como `postgres.<project-ref>` (no solo `postgres`).
4. Pega esa cadena completa (con tu password) en `DATABASE_URL` dentro de `.env`.

Al arrancar, `agent/graph.py` crea el pool de conexiones y corre `PostgresSaver.setup()` una sola vez (crea sus propias tablas de checkpoint en Postgres si no existen; en corridas siguientes no hace nada). Si `DATABASE_URL` esta mal o no resuelve, el backend falla al arrancar con un error claro apenas intenta importar `routes.chat_routes`.

### Firebase (notificaciones push)

Para que las notificaciones push funcionen en local, hay dos opciones (ver `services/push_notifications.py`):

1. Colocar el archivo de credenciales de servicio de Firebase como `firebase-credentials.json` en la raiz del proyecto (no se sube a git), o
2. Pegar el contenido completo de ese JSON como texto en la variable `FIREBASE_CREDENTIALS_JSON` del `.env` (asi se hace en produccion, por ejemplo en Railway).

Si ninguna de las dos esta configurada, el resto del backend sigue funcionando; simplemente no se enviaran notificaciones push.

## Ejecutar el proyecto

```bash
uvicorn main:app --reload
```

El servidor queda disponible en `http://localhost:8000`. Al arrancar tambien se inicia un scheduler en segundo plano (APScheduler) que revisa cada 5 minutos si hay recordatorios de citas pendientes por enviar.

Documentacion interactiva de la API (Swagger UI) disponible en `http://localhost:8000/docs`.

## Chat API

Endpoints publicos (sin autenticacion — el enlace es la unica "credencial") aislados por negocio, pensados para que un frontend de cliente final los consuma. El aislamiento entre negocios esta garantizado en dos capas: el `business_id` en la URL, y el `thread_id` interno del checkpointer (`business_id:session_id`), asi que un `session_id` jamas puede continuar la conversacion de otro negocio.

| Metodo y ruta | Que hace |
|---|---|
| `GET /chat/{business_id}/config` | Info publica para que el widget arranque: `{business_id, name, business_type, enabled}`. `enabled` es `false` si el negocio tiene `plan == "none"` o esta bloqueado (`blocked`). 404 si el negocio no existe. |
| `POST /chat/{business_id}/sessions` | Crea una sesion de chat nueva (conversacion vacia) y devuelve `{"session_id": "<uuid>"}`. El frontend la guarda (ej. `localStorage`) y la reutiliza en cada mensaje de esa visita; para empezar de cero, crea otra sesion. |
| `POST /chat/{business_id}/sessions/{session_id}/messages` | Body `{"mensaje": "..."}` → `{"respuesta": "..."}`. Manda el mensaje del cliente al agente y devuelve su respuesta. En este chat general, si el negocio tiene mas de un empleado activo, el propio agente le pregunta al cliente con quien quiere la cita. |
| `GET /chat/{business_id}/sessions/{session_id}/messages` | `{"session_id", "mensajes": [{"role": "user"|"assistant", "content": "..."}]}`. Historial de la sesion, util para recargarlo si el cliente refresca la pagina. |

Cada empleado tiene ademas su propio enlace de chat, con el mismo contrato pero el empleado ya fijo (el agente no pregunta, y solo ofrece los servicios de ese empleado): `GET /chat/{business_id}/{employee_id}/config` (incluye `employee_id`/`employee_name`), `POST /chat/{business_id}/{employee_id}/sessions`, `POST /chat/{business_id}/{employee_id}/sessions/{session_id}/messages`, `GET /chat/{business_id}/{employee_id}/sessions/{session_id}/messages`. 404 si el empleado no existe, no es de ese negocio, o esta inactivo.

Ejemplo de flujo completo con `curl`:

```bash
BUSINESS_ID="<id-de-un-negocio-existente>"

# 1. El frontend chequea si el negocio tiene el chat activo
curl "http://localhost:8000/chat/$BUSINESS_ID/config"

# 2. Crea una sesion nueva
SESSION_ID=$(curl -s -X POST "http://localhost:8000/chat/$BUSINESS_ID/sessions" | python3 -c "import sys,json;print(json.load(sys.stdin)['session_id'])")

# 3. Conversa
curl -X POST "http://localhost:8000/chat/$BUSINESS_ID/sessions/$SESSION_ID/messages" \
  -H "Content-Type: application/json" \
  -d '{"mensaje": "Hola, quiero agendar una cita"}'

# 4. Recarga el historial (ej. al refrescar la pagina)
curl "http://localhost:8000/chat/$BUSINESS_ID/sessions/$SESSION_ID/messages"
```

El agente (`agent/`) identifica al cliente por su numero de WhatsApp/celular, nunca por cedula: lo pide en la conversacion cuando hace falta (agendar, consultar, cancelar o reprogramar una cita) y lo guarda en el estado de la sesion mediante la tool `registrar_telefono_cliente`, asi no hay que repetirlo en cada mensaje. Todas las acciones del agente (consultar servicios/horas, agendar, consultar/cancelar/reprogramar citas, transferir a una persona) pasan por tools — ver `agent/tools.py`.

**Nota:** el bot de IA conversacional ya no responde por WhatsApp. `POST /webhook` (Meta) y `POST /baileys/message` siguen activos pero ya no invocan al agente; WhatsApp queda solo para recordatorios salientes y la confirmacion automatica de citas agendadas en la web.

## Variables de entorno

Ver `.env.example` para la lista completa con descripciones. Resumen:

| Variable | Para que sirve |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | Conexion a Supabase (REST API) |
| `SUPABASE_SERVICE_ROLE_KEY` | Clave server-side recomendada para operaciones backend que deben bypassear RLS |
| `DATABASE_URL` | Conexion Postgres directa (Session pooler) para el checkpointer del agente de chat — ver seccion arriba |
| `OPENAI_API_KEY` | Motor de IA del agente de citas |
| `CHAT_MODEL_NAME` | Modelo de OpenAI a usar (opcional, default `gpt-4.1-mini`) |
| `WHATSAPP_TOKEN` | Token del System User de Meta para enviar mensajes salientes por WhatsApp Cloud API |
| `VERIFY_TOKEN` | Token propio para validar el webhook de Meta |
| `BAILEYS_SERVICE_URL`, `BAILEYS_INTERNAL_API_KEY` | URL y api key compartida con el microservicio de Baileys (WhatsApp no oficial), usado para recordatorios y vinculacion |
| `CORS_ALLOWED_ORIGINS` | Origenes permitidos del panel admin (lista separada por comas) |
| `FIREBASE_CREDENTIALS_JSON` | Credenciales de Firebase para notificaciones push (alternativa a `firebase-credentials.json`) |

## Estructura del proyecto

```
main.py               # arranque de FastAPI, registro de routers, scheduler de recordatorios
core/                  # configuracion compartida (settings del agente de chat)
agent/                 # agente conversacional de LangGraph: estado, tools, prompt, grafo
routes/                # un router por recurso (chat, webhook, negocios, servicios, horarios, etc)
services/               # logica de negocio: Supabase, WhatsApp, Baileys, push, uso de IA, scheduling
```

Para una guia mas detallada de la arquitectura (flujo del agente, aislamiento por negocio, modelo de auth, etc), ver `CLAUDE.md`.
