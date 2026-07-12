from datetime import datetime, timedelta
from services.db import supabase
from services.whatsapp import send_whatsapp_message
from services.baileys_client import send_baileys_message
from services.ai_agent import _formatear_fecha_natural


def revisar_y_enviar_recordatorios():
    """
    Se ejecuta periodicamente (cada pocos minutos). Busca citas confirmadas
    cuyo momento de recordatorio ya llego (segun reminder_hours_before del negocio)
    y que aun no se les ha enviado el recordatorio, y lo manda por WhatsApp.
    """
    ahora = datetime.now()

    # Trae todos los negocios con su configuracion de recordatorio
    negocios_response = supabase.table("businesses").select("id, name, reminder_hours_before, whatsapp_phone_number_id").execute()
    negocios = negocios_response.data

    total_enviados = 0

    for negocio in negocios:
        business_id = negocio["id"]
        nombre_negocio = negocio["name"]
        horas_antes = negocio.get("reminder_hours_before") or 24
        whatsapp_phone_number_id = negocio.get("whatsapp_phone_number_id")

        # Canal de envio: si el negocio tiene numero oficial de Meta se usa
        # ese; si no, se intenta por Baileys (numero vinculado con codigo).
        # Los negocios sin ninguno de los dos simplemente fallaran el envio
        # y el recordatorio se reintentara en el siguiente ciclo.

        # Ventana: citas que caen entre AHORA y AHORA + horas_antes,
        # que aun no han recibido recordatorio, y siguen confirmadas.
        limite_superior = ahora + timedelta(hours=horas_antes)

        citas_response = (
            supabase.table("appointments")
            .select("*, services(name)")
            .eq("business_id", business_id)
            .eq("status", "confirmed")
            .eq("reminder_sent", False)
            .gte("scheduled_at", ahora.isoformat())
            .lte("scheduled_at", limite_superior.isoformat())
            .execute()
        )

        for cita in citas_response.data:
            try:
                fecha_cita = datetime.fromisoformat(cita["scheduled_at"])
                nombre_cliente = cita.get("client_name") or "Cliente"
                nombre_servicio = cita.get("services", {}).get("name", "tu cita") if cita.get("services") else "tu cita"

                mensaje = (
                    f"¡Hola, {nombre_cliente}! 👋\n\n"
                    f"Te recordamos tu próxima cita en *{nombre_negocio}* 📅\n\n"
                    f"✨ Servicio: *{nombre_servicio}*\n"
                    f"🕐 Cuándo: *{_formatear_fecha_natural(fecha_cita)}*\n\n"
                    "¡Te esperamos! 😊"
                )

                if whatsapp_phone_number_id:
                    resultado_envio = send_whatsapp_message(
                        to=cita["client_phone"],
                        text=mensaje,
                        business_phone_number_id=whatsapp_phone_number_id,
                    )
                else:
                    resultado_envio = send_baileys_message(
                        business_id=business_id,
                        to=cita["client_phone"],
                        text=mensaje,
                    )

                # Solo marcamos como enviado si WhatsApp confirmo el envio (sin campo "error")
                if isinstance(resultado_envio, dict) and "error" in resultado_envio:
                    print(f"Fallo el envio de recordatorio para cita {cita['id']}: {resultado_envio['error']}")
                    continue

                supabase.table("appointments").update({"reminder_sent": True}).eq("id", cita["id"]).execute()

                total_enviados += 1
                print(f"Recordatorio enviado a {cita['client_phone']} para cita {cita['id']}")

            except Exception as e:
                print(f"Error enviando recordatorio para cita {cita.get('id')}: {e}")

    if total_enviados > 0:
        print(f"Revision de recordatorios completada. Enviados: {total_enviados}")

    return total_enviados