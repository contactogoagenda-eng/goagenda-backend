from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from services.auth import obtener_usuario_actual
from services.qr_card import generar_tarjeta_qr

router = APIRouter(tags=["qr-card"])


class QrCardRequest(BaseModel):
    chat_link: str
    business_name: str
    whatsapp: str


@router.post("/qr-card")
def generar_qr_card(data: QrCardRequest, user_id: str = Depends(obtener_usuario_actual)):
    """
    Genera una tarjeta PNG lista para imprimir con el QR del enlace del
    chat (colores del branding), el nombre y whatsapp del negocio, y el
    logo de la app. Protegido igual que los demas endpoints de la app del
    dueño: requiere el JWT de Supabase Auth.
    """
    chat_link = data.chat_link.strip()
    business_name = data.business_name.strip()
    whatsapp = data.whatsapp.strip()
    if not chat_link or not business_name or not whatsapp:
        raise HTTPException(status_code=400, detail="chat_link, business_name y whatsapp son obligatorios")

    imagen_png = generar_tarjeta_qr(chat_link, business_name, whatsapp)
    return Response(content=imagen_png, media_type="image/png")
