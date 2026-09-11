from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from services.auth import obtener_usuario_actual, verificar_acceso_negocio
from services.db import supabase
from services.qr_card import generar_tarjeta_qr

router = APIRouter(tags=["qr-card"])


class QrCardRequest(BaseModel):
    business_id: str
    chat_link: str


@router.post("/qr-card")
def generar_qr_card(data: QrCardRequest, user_id: str = Depends(obtener_usuario_actual)):
    """
    Genera una tarjeta PNG lista para imprimir con el QR del enlace del
    chat (colores del branding), el nombre y whatsapp del negocio, y el
    logo de la app. Protegido igual que los demas endpoints de lectura de
    negocio: dueño o empleado activo de `business_id`.

    El nombre y el whatsapp que se imprimen en la tarjeta se leen del
    negocio en la base de datos (nunca del cliente): antes se recibian
    directo del body de la request sin verificar que correspondieran al
    negocio del usuario autenticado, lo que permitia a cualquier cuenta
    valida generar una tarjeta con la marca de OTRO negocio.
    """
    verificar_acceso_negocio(data.business_id, user_id)

    chat_link = data.chat_link.strip()
    if not chat_link:
        raise HTTPException(status_code=400, detail="chat_link es obligatorio")

    negocio = supabase.table("businesses").select("name, phone_number").eq("id", data.business_id).execute()
    if not negocio.data:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")

    business_name = (negocio.data[0].get("name") or "").strip()
    whatsapp = (negocio.data[0].get("phone_number") or "").strip()
    if not business_name or not whatsapp:
        raise HTTPException(status_code=400, detail="El negocio no tiene nombre o whatsapp configurado")

    imagen_png = generar_tarjeta_qr(chat_link, business_name, whatsapp)
    return Response(content=imagen_png, media_type="image/png")
