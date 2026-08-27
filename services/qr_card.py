"""
Genera la tarjeta QR imprimible que un negocio pega en su mostrador para
que sus clientes escaneen y entren directo al chat de agendamiento (mismo
concepto que los standees de QR de negocios de Bancolombia): QR grande
arriba con los colores del branding, debajo el nombre y whatsapp del
negocio, y el logo de la app pequeño y centrado abajo.
"""

import io
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

ANCHO = 1200
ALTO = 1600

TEAL = "#03a580"
NAVY = "#0e182c"
CHARCOAL = "#1d293d"
BLANCO = "#ffffff"

_RAIZ = Path(__file__).resolve().parent.parent
_FUENTE_BOLD = str(_RAIZ / "static" / "fonts" / "DejaVuSans-Bold.ttf")
_LOGO_PATH = _RAIZ / "static" / "images" / "Logo.png"


def _fuente(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FUENTE_BOLD, size)


def _texto_centrado(draw: ImageDraw.ImageDraw, y: float, texto: str, fuente: ImageFont.FreeTypeFont, color: str) -> float:
    """Dibuja texto centrado horizontalmente en el ancho de la tarjeta y devuelve su alto."""
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    ancho_texto = bbox[2] - bbox[0]
    x = (ANCHO - ancho_texto) / 2 - bbox[0]
    draw.text((x, y - bbox[1]), texto, font=fuente, fill=color)
    return bbox[3] - bbox[1]


def _fuente_que_encaja(
    draw: ImageDraw.ImageDraw, texto: str, tam_inicial: int, tam_minimo: int, ancho_maximo: float
) -> ImageFont.FreeTypeFont:
    """
    Nombre del negocio y whatsapp los escribe el dueño, sin limite de
    longitud: si se usara un tamaño fijo, un nombre largo se saldria de
    los bordes de la tarjeta. Baja el tamaño de fuente hasta que el texto
    quepa en el ancho disponible (o hasta el minimo, como ultimo recurso).
    """
    tam = tam_inicial
    while tam > tam_minimo:
        fuente = _fuente(tam)
        bbox = draw.textbbox((0, 0), texto, font=fuente)
        if bbox[2] - bbox[0] <= ancho_maximo:
            return fuente
        tam -= 2
    return _fuente(tam_minimo)


def generar_tarjeta_qr(chat_link: str, nombre_negocio: str, whatsapp: str) -> bytes:
    lienzo = Image.new("RGB", (ANCHO, ALTO), BLANCO)
    draw = ImageDraw.Draw(lienzo)

    margen = 28
    radio_tarjeta = 32
    draw.rounded_rectangle(
        [margen, margen, ANCHO - margen, ALTO - margen],
        radius=radio_tarjeta,
        outline=TEAL,
        width=3,
    )

    # --- Encabezado navy (solo esquinas superiores redondeadas) ---
    alto_encabezado = 200
    draw.rounded_rectangle(
        [margen, margen, ANCHO - margen, margen + alto_encabezado],
        radius=radio_tarjeta,
        fill=NAVY,
        corners=(True, True, False, False),
    )
    _texto_centrado(draw, margen + (alto_encabezado - 60) / 2, "ESCANEA Y AGENDA TU CITA", _fuente(50), BLANCO)

    # --- QR con los colores del branding ---
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=14, border=2)
    qr.add_data(chat_link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=NAVY, back_color=BLANCO).convert("RGB")

    qr_tam = 760
    qr_img = qr_img.resize((qr_tam, qr_tam), Image.NEAREST)

    marco_pad = 24
    qr_y = margen + alto_encabezado + 60
    draw.rounded_rectangle(
        [
            (ANCHO - qr_tam) / 2 - marco_pad,
            qr_y - marco_pad,
            (ANCHO + qr_tam) / 2 + marco_pad,
            qr_y + qr_tam + marco_pad,
        ],
        radius=24,
        outline=TEAL,
        width=3,
        fill=BLANCO,
    )
    lienzo.paste(qr_img, (int((ANCHO - qr_tam) / 2), int(qr_y)))

    # --- Tarjeta con los datos del negocio: nombre y whatsapp ---
    ancho_util = ANCHO - 2 * margen - 160
    y = qr_y + qr_tam + marco_pad + 55
    fuente_nombre = _fuente_que_encaja(draw, nombre_negocio, 56, 30, ancho_util)
    y += _texto_centrado(draw, y, nombre_negocio, fuente_nombre, CHARCOAL) + 45

    texto_whats = f"WhatsApp: {whatsapp}"
    fuente_whats = _fuente_que_encaja(draw, texto_whats, 38, 24, ancho_util - 80)
    bbox = draw.textbbox((0, 0), texto_whats, font=fuente_whats)
    ancho_txt, alto_txt = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 40, 22
    pill_ancho = ancho_txt + pad_x * 2
    pill_alto = alto_txt + pad_y * 2
    pill_x0 = (ANCHO - pill_ancho) / 2
    draw.rounded_rectangle(
        [pill_x0, y, pill_x0 + pill_ancho, y + pill_alto],
        radius=pill_alto / 2,
        fill=TEAL,
    )
    draw.text((pill_x0 + pad_x - bbox[0], y + pad_y - bbox[1]), texto_whats, font=fuente_whats, fill=BLANCO)
    y += pill_alto + 55

    draw.line([(margen + 80, y), (ANCHO - margen - 80, y)], fill=TEAL, width=3)

    # --- Logo de la app: pequeño y centrado abajo ---
    logo = Image.open(_LOGO_PATH).convert("RGBA")
    logo_ancho_objetivo = 260
    ratio = logo_ancho_objetivo / logo.width
    logo = logo.resize((logo_ancho_objetivo, int(logo.height * ratio)), Image.LANCZOS)
    logo_x = (ANCHO - logo.width) // 2
    logo_y = ALTO - margen - 55 - logo.height
    lienzo.paste(logo, (logo_x, logo_y), logo)

    buffer = io.BytesIO()
    lienzo.save(buffer, format="PNG")
    return buffer.getvalue()
