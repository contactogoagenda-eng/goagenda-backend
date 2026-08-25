from datetime import datetime
from services.db import supabase
from services.push_notifications import enviar_notificacion_nueva_cita

# Precios oficiales de OpenAI para gpt-4.1-mini (verificado junio 2026)
# https://openai.com/api/pricing
PRECIO_INPUT_POR_MILLON = 0.40
PRECIO_OUTPUT_POR_MILLON = 1.60


def calcular_costo(prompt_tokens: int, completion_tokens: int) -> float:
    costo_input = (prompt_tokens / 1_000_000) * PRECIO_INPUT_POR_MILLON
    costo_output = (completion_tokens / 1_000_000) * PRECIO_OUTPUT_POR_MILLON
    return round(costo_input + costo_output, 6)


def registrar_uso(business_id: str, prompt_tokens: int, completion_tokens: int, total_tokens: int) -> float:
    """
    Guarda en ai_usage_log el consumo de una llamada al modelo de IA.
    Retorna el costo estimado de esta llamada especifica.
    """
    costo = calcular_costo(prompt_tokens, completion_tokens)

    try:
        supabase.table("ai_usage_log").insert({
            "business_id": business_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": costo,
        }).execute()
    except Exception as e:
        print(f"Error guardando registro de uso de IA: {e}")

    _verificar_presupuesto(business_id)
    return costo


def obtener_gasto_mensual(business_id: str) -> float:
    """Suma el costo estimado de todas las llamadas del mes actual para un negocio."""
    inicio_mes = datetime.now().strftime("%Y-%m-01")
    response = (
        supabase.table("ai_usage_log")
        .select("estimated_cost_usd")
        .eq("business_id", business_id)
        .gte("created_at", inicio_mes)
        .execute()
    )
    return round(sum(row["estimated_cost_usd"] or 0 for row in response.data), 4)


def _verificar_presupuesto(business_id: str):
    """
    Si el gasto del mes ya supera el 80% del presupuesto configurado,
    envia una notificacion push UNA SOLA VEZ por mes al dueño del negocio.
    """
    business_response = (
        supabase.table("businesses")
        .select("monthly_ai_budget_usd, ai_budget_alert_sent_month, fcm_token, name")
        .eq("id", business_id)
        .execute()
    )
    if not business_response.data:
        return

    business = business_response.data[0]
    presupuesto = business.get("monthly_ai_budget_usd") or 5.00
    mes_actual = datetime.now().strftime("%Y-%m")

    if business.get("ai_budget_alert_sent_month") == mes_actual:
        return  # ya se avisó este mes, no repetir

    gasto_actual = obtener_gasto_mensual(business_id)

    if gasto_actual >= presupuesto * 0.8:
        fcm_token = business.get("fcm_token")
        if fcm_token:
            try:
                enviar_notificacion_nueva_cita(
                    fcm_token=fcm_token,
                    nombre_cliente="Alerta de presupuesto",
                    servicio=f"Ya gastaste ${gasto_actual} USD de tu presupuesto mensual de ${presupuesto} USD en IA",
                    fecha_hora_texto="este mes",
                )
            except Exception as e:
                print(f"Error enviando alerta de presupuesto: {e}")

        supabase.table("businesses").update({
            "ai_budget_alert_sent_month": mes_actual
        }).eq("id", business_id).execute()