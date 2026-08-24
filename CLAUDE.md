# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GoAgenda backend: a FastAPI service that runs an AI-driven WhatsApp booking assistant for multiple businesses (salons, barbershops, spas, etc). Each business connects its own WhatsApp number (either an official Meta Cloud API number, or an unofficial number via a separate Baileys microservice), and the bot in `services/ai_agent.py` handles the conversation: answering questions about services/hours, booking, cancelling, and rescheduling appointments, and handing off to a human on request. A companion Flutter app (not in this repo) is the business owner's dashboard, and a separate web app (`BOOKING_WEB_URL`, referenced but not in this repo) is used for self-service booking on the "basic" plan.

Data lives in Supabase (Postgres + Auth). There is no ORM — all queries go through the `supabase-py` client directly in `services/db.py` or inline in route files.

## Running locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Requires a `.env` file (not committed) with at least: `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`, `WHATSAPP_TOKEN`, `VERIFY_TOKEN`, `BAILEYS_SERVICE_URL`, `BAILEYS_INTERNAL_API_KEY`, `BOOKING_WEB_URL`, and either `FIREBASE_CREDENTIALS_JSON` (production, JSON pasted as a string) or a local `firebase-credentials.json` file for push notifications.

There is no test suite, linter, or build step configured in this repo. This is a pure Python/FastAPI project — there is no Node.js code or tooling here. The Baileys WhatsApp connector (`@whiskeysockets/baileys`) is a separate Node service that lives outside this repo and is only ever talked to over HTTP via `BAILEYS_SERVICE_URL` (see `services/baileys_client.py`); do not add `package.json`/`node_modules` back to this repo for it.

To manually exercise the AI conversation flow without a real WhatsApp connection, use the `/simulate-message` endpoint (see `routes/simulate.py`), which requires the internal `x-api-key` header (`requiere_api_key_interna`).

## Architecture

**Two parallel WhatsApp entry points feed the same conversation engine.** Meta's official Cloud API delivers messages via `POST /webhook` (`routes/webhook.py`), identifying the business by `phone_number_id` embedded in the payload. The Baileys (unofficial) path delivers messages via `POST /baileys/message` (`routes/baileys_routes.py`), where the Baileys microservice includes `business_id` directly since it already resolved the connection. Both call `services/ai_agent.py:procesar_mensaje` with an in-memory conversation history dict keyed by `f"{business_id}:{client_phone}"` — history is per-process and lost on restart; there's no persistence layer for it. `services/reminder_service.py` mirrors this dual-channel logic when sending appointment reminders: it uses Meta if the business has `whatsapp_phone_number_id`, otherwise falls back to Baileys via `services/baileys_client.py`.

**`services/ai_agent.py` is the core of the system** (~900 lines) and the first place to look for booking/conversation behavior. Key pieces:
- `procesar_mensaje` is the entry point. It first handles special cases outside the LLM entirely: excluded chats (`esta_chat_excluido`), the "none" plan (bot fully disabled), auto-confirming appointments booked via the web (matched by a hardcoded confirmation phrase from the client), and a menu-based intent gate for new/expired conversations (`VENTANA_CONVERSACION_ACTIVA_HORAS` = 2h) that decides whether to hand off to a human, redirect "basic" plan users to the booking web link, or continue into the full LLM flow.
- The LLM flow uses OpenAI function calling (`gpt-4.1-mini`, model pinned in `MODEL_NAME`) with a `TOOLS` list and `ejecutar_tool` dispatcher for: `crear_cita`, `consultar_citas_cliente`, `cancelar_cita`, `reprogramar_cita`, `consultar_horas_disponibles`, `consultar_servicios_disponibles`, `transferir_a_equipo`. The system prompt (`system_instruction`) encodes most of the actual product behavior — timezone/format rules, confirmation-before-booking requirements, anti-prompt-injection rules, tone per business type, and WhatsApp-specific formatting (single-asterisk bold, no headers). Changing bot behavior usually means editing this prompt, not the Python logic around it.
- Availability/scheduling logic (`_generar_horas_disponibles`, `_es_hora_valida`, `_hay_choque_de_horario`) is shared between the AI tools and the manual booking endpoint (`routes/manual_appointments.py`) so both channels enforce the same business hours, lunch break, and conflict rules. All times are handled as naive `datetime`s assumed to be in `America/Bogota` (`TZ_NEGOCIO`) — the server itself runs in UTC (Railway), so `_ahora_local()` exists specifically to avoid "today"/"tomorrow" being computed a day off after 7pm local time.
- Every OpenAI call's token usage is logged via `services/ai_usage_tracking.py:registrar_uso`, which also triggers a one-time-per-month push alert when a business crosses 80% of its configured `monthly_ai_budget_usd`.

**Auth model** (`services/auth.py`): two independent mechanisms, not interchangeable.
- End-user requests (from the Flutter app) carry a Supabase Auth JWT in `Authorization: Bearer <token>`, validated by `obtener_usuario_actual`. Ownership is then checked per-request with `verificar_dueno(business_id, user_id)` against the `businesses.owner_id` column — almost every business-scoped route depends on both.
- Server-to-server calls (the Baileys microservice calling back into this backend, plus internal/test endpoints) use a shared secret in the `x-api-key` header, validated by `requiere_api_key_interna` against `BAILEYS_INTERNAL_API_KEY`.

**Push notifications** (`services/push_notifications.py`) go through Firebase Cloud Messaging to the business owner's device (not the client), for events like new/cancelled/rescheduled appointments and budget alerts. Firebase Admin is a lazy singleton initialized from either `FIREBASE_CREDENTIALS_JSON` (prod) or a local `firebase-credentials.json` file.

**Plans gate bot behavior**, read from `businesses.plan`: `"none"` disables the AI bot entirely (manual-only agenda), `"basic"` redirects WhatsApp users to the self-service booking web (`BOOKING_WEB_URL`) instead of letting the LLM book directly, and any other value runs the full conversational booking flow.

**Reminders** run on an in-process `APScheduler` `BackgroundScheduler` started in `main.py` (5-minute interval, not a separate worker process), calling `services/reminder_service.py:revisar_y_enviar_recordatorios`. Each business configures its own `reminder_hours_before` lead time.

## Conventions

- Function/variable names, docstrings, and print statements are in Spanish throughout the codebase (domain language: `cita` = appointment, `negocio` = business, `horario` = hours, `recordatorio` = reminder); comments explain *why*, matching the existing style — follow this rather than switching to English.
- Route modules follow a flat pattern: one `APIRouter()` per resource in `routes/`, registered directly in `main.py`, with Pydantic models defined inline in the route file rather than in a shared schemas module.
- Deletion is soft where it would otherwise break referential history (e.g. `services` are marked `active: False` rather than removed, since existing appointments reference them).
- New Supabase tables/migrations in this repo are checked in as standalone `.sql` files at the repo root (see `excluded_chats.sql`) meant to be run manually via the Supabase SQL editor — there's no migration framework.
