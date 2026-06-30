from fastapi import FastAPI
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

from routes.webhook import router as webhook_router
from routes.simulate import router as simulate_router
from routes.services_routes import router as services_router
from routes.business_hours_routes import router as business_hours_router
from routes.business_settings_routes import router as business_settings_router
from services.db import supabase
from services.reminder_service import revisar_y_enviar_recordatorios
from routes.manual_appointments import router as manual_appointments_router


load_dotenv()

app = FastAPI(title="GoAgenda API")

app.include_router(webhook_router)
app.include_router(simulate_router)
app.include_router(services_router)
app.include_router(business_hours_router)
app.include_router(business_settings_router)
app.include_router(manual_appointments_router)


@app.get("/")
def root():
    return {"status": "GoAgenda backend corriendo correctamente"}


@app.get("/test-db")
def test_db():
    """Endpoint de prueba para confirmar que la conexion a Supabase funciona."""
    response = supabase.table("businesses").select("*").execute()
    return {"businesses": response.data}

@app.get("/ai-usage")
def consultar_gasto_ia(business_id: str):
    """Consulta el gasto estimado de IA del negocio en el mes actual."""
    from services.ai_usage_tracking import obtener_gasto_mensual

    business_response = supabase.table("businesses").select("monthly_ai_budget_usd, name").eq("id", business_id).execute()
    if not business_response.data:
        return {"error": "Negocio no encontrado"}

    presupuesto = business_response.data[0].get("monthly_ai_budget_usd") or 5.00
    gasto = obtener_gasto_mensual(business_id)

    return {
        "negocio": business_response.data[0]["name"],
        "gasto_actual_usd": gasto,
        "presupuesto_mensual_usd": presupuesto,
        "porcentaje_usado": round((gasto / presupuesto) * 100, 1) if presupuesto > 0 else 0,
    }

@app.post("/test-push")
def test_push(business_id: str):
    """Prueba el envio de notificacion push sin pasar por la IA (no gasta cuota de Gemini)."""
    from services.push_notifications import enviar_notificacion_nueva_cita

    response = supabase.table("businesses").select("*").eq("id", business_id).execute()
    if not response.data:
        return {"error": "Negocio no encontrado"}

    business = response.data[0]
    fcm_token = business.get("fcm_token")

    if not fcm_token:
        return {"error": "Este negocio no tiene fcm_token registrado todavia. Abre la app primero."}

    enviar_notificacion_nueva_cita(
        fcm_token=fcm_token,
        nombre_cliente="Cliente de Prueba",
        servicio="Corte de cabello",
        fecha_hora_texto="hoy a las 15:00",
    )
    return {"status": "Notificacion enviada (revisa tu celular)"}


@app.post("/test-reminders")
def test_reminders():
    """Endpoint para probar manualmente el envio de recordatorios sin esperar al scheduler."""
    enviados = revisar_y_enviar_recordatorios()
    return {"recordatorios_enviados": enviados}


# ---------------------------------------------------------
# Scheduler: revisa cada 5 minutos si hay recordatorios pendientes
# ---------------------------------------------------------
scheduler = BackgroundScheduler()
scheduler.add_job(revisar_y_enviar_recordatorios, "interval", minutes=5)
scheduler.start()

atexit.register(lambda: scheduler.shutdown())