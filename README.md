# GoAgenda Backend

API en FastAPI que atiende el bot de citas por WhatsApp de GoAgenda. Cada negocio conecta su propio numero de WhatsApp (Meta Cloud API oficial, o un numero vinculado via un microservicio Baileys aparte), y el backend responde automaticamente a los clientes: informa servicios y horarios, agenda, cancela y reprograma citas, y envia recordatorios y notificaciones push al dueño del negocio.

Proyecto 100% Python. No requiere Node ni npm para correr.

## Requisitos

- Python 3.9+
- Una cuenta/proyecto de Supabase (base de datos + auth)
- Una API key de OpenAI
- Credenciales de Firebase (para las notificaciones push), opcional en desarrollo

## Instalacion

```bash
# 1. Clonar el repo y entrar a la carpeta
git clone <url-del-repo>
cd goagenda-backend

# 2. Crear y activar un entorno virtual
python3 -m venv venv
source venv/bin/activate   # en Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# luego edita .env y completa los valores reales
```

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

## Probar el bot sin WhatsApp real

El endpoint `POST /simulate-message` permite simular una conversacion de WhatsApp sin necesitar la API de Meta ni Baileys funcionando. Requiere el header `x-api-key` con el valor de `BAILEYS_INTERNAL_API_KEY`:

```bash
curl -X POST http://localhost:8000/simulate-message \
  -H "Content-Type: application/json" \
  -H "x-api-key: <BAILEYS_INTERNAL_API_KEY>" \
  -d '{"business_id": "<id-de-un-negocio-existente>", "client_phone": "573001112233", "mensaje": "Hola"}'
```

Para reiniciar el historial de esa conversacion simulada: `POST /simulate-reset?business_id=...&client_phone=...`.

## Variables de entorno

Ver `.env.example` para la lista completa con descripciones. Resumen:

| Variable | Para que sirve |
|---|---|
| `SUPABASE_URL`, `SUPABASE_KEY` | Conexion a la base de datos y auth de Supabase |
| `OPENAI_API_KEY` | Motor de IA del bot de citas (function calling) |
| `WHATSAPP_TOKEN` | Token del System User de Meta para enviar mensajes por WhatsApp Cloud API |
| `VERIFY_TOKEN` | Token propio para validar el webhook de Meta |
| `BAILEYS_SERVICE_URL`, `BAILEYS_INTERNAL_API_KEY` | URL y api key compartida con el microservicio de Baileys (WhatsApp no oficial) |
| `BOOKING_WEB_URL` | URL de la web de agendamiento self-service (plan "basic") |
| `FIREBASE_CREDENTIALS_JSON` | Credenciales de Firebase para notificaciones push (alternativa a `firebase-credentials.json`) |

## Estructura del proyecto

```
main.py               # arranque de FastAPI, registro de routers, scheduler de recordatorios
routes/                # un router por recurso (webhook, negocios, servicios, horarios, etc)
services/               # logica de negocio: IA del bot, Supabase, WhatsApp, Baileys, push, uso de IA
```

Para una guia mas detallada de la arquitectura (flujo de mensajes, tools de la IA, modelo de auth, etc), ver `CLAUDE.md`.
