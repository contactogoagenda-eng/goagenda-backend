"""
Genera la tarjeta QR imprimible que un negocio pega en su mostrador para
que sus clientes escaneen y entren directo al chat de agendamiento (mismo
concepto que los standees de QR de negocios de Bancolombia): logo arriba,
titular invitando a escanear, el QR grande centrado, el nombre del negocio
en una pastilla que hace de puente con la franja ondulada inferior, y ahi
tres beneficios (agendar, recordatorios, sin esperas) sobre el color de
marca.
"""

import io
import math
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

ANCHO = 1200
ALTO = 2080

TEAL = "#03a580"
NAVY = "#0e182c"
CHARCOAL = "#1d293d"
GRIS = "#6b7280"
BLANCO = "#ffffff"

ONDA_COLOR_ARRIBA = (0x09, 0xBD, 0x81)
ONDA_COLOR_ABAJO = (0x15, 0x8A, 0x63)

_RAIZ = Path(__file__).resolve().parent.parent
_FUENTE_BOLD = str(_RAIZ / "static" / "fonts" / "DejaVuSans-Bold.ttf")
_FUENTE_REGULAR = str(_RAIZ / "static" / "fonts" / "DejaVuSans.ttf")
_LOGO_PATH = _RAIZ / "static" / "images" / "Logo-dark.png"
_ICONOS_DIR = _RAIZ / "static" / "images" / "icons"

# Mismos iconos (Lucide, trazo blanco) que usa el frontend para estos 3
# beneficios, rasterizados una sola vez a PNG porque el backend no tiene
# un renderizador de SVG.
BENEFICIOS = [
    ("calendar-check", ("Agenda", "en segundos")),
    ("bell", ("Recordatorios", "automaticos")),
    ("shield-check", ("Sin", "Esperas")),
]

AMPLITUD_ONDA = 42


def _icono(nombre: str, tam: int) -> Image.Image:
    icono = Image.open(_ICONOS_DIR / f"{nombre}.png").convert("RGBA")
    return icono.resize((tam, tam), Image.LANCZOS)


def _gradiente_vertical(ancho: int, alto: int, color_arriba: tuple, color_abajo: tuple) -> Image.Image:
    gradiente = Image.new("RGB", (1, alto))
    for fila in range(alto):
        t = fila / max(alto - 1, 1)
        color = tuple(round(color_arriba[i] + (color_abajo[i] - color_arriba[i]) * t) for i in range(3))
        gradiente.putpixel((0, fila), color)
    return gradiente.resize((ancho, alto))


def _fuente(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FUENTE_BOLD, size)


def _fuente_regular(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FUENTE_REGULAR, size)


def _texto_centrado(
    draw: ImageDraw.ImageDraw, y: float, texto: str, fuente: ImageFont.FreeTypeFont, color: str, cx: float = ANCHO / 2
) -> float:
    """Dibuja texto centrado horizontalmente en cx y devuelve su alto."""
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    ancho_texto = bbox[2] - bbox[0]
    x = cx - ancho_texto / 2 - bbox[0]
    draw.text((x, y - bbox[1]), texto, font=fuente, fill=color)
    return bbox[3] - bbox[1]


def _fuente_que_encaja(
    draw: ImageDraw.ImageDraw, texto: str, tam_inicial: int, tam_minimo: int, ancho_maximo: float
) -> ImageFont.FreeTypeFont:
    """
    El nombre del negocio lo escribe el dueño, sin limite de longitud: si
    se usara un tamaño fijo, un nombre largo se saldria de los bordes de
    la tarjeta. Baja el tamaño de fuente hasta que el texto quepa en el
    ancho disponible (o hasta el minimo, como ultimo recurso).
    """
    tam = tam_inicial
    while tam > tam_minimo:
        fuente = _fuente(tam)
        bbox = draw.textbbox((0, 0), texto, font=fuente)
        if bbox[2] - bbox[0] <= ancho_maximo:
            return fuente
        tam -= 2
    return _fuente(tam_minimo)


def _dibujar_onda_con_beneficios(lienzo: Image.Image, draw: ImageDraw.ImageDraw, margen: float, radio_tarjeta: float, y_base: float) -> None:
    """
    Franja inferior con degradado de marca y borde superior ondulado (dos
    lomos), como un "standee" de mostrador. La silueta (rectangulo con
    esquinas inferiores redondeadas, recortado por la curva senoidal en la
    parte de arriba) se arma como mascara y se usa para pegar un degradado
    vertical, ya que PIL no soporta fill de gradiente en formas.
    """
    y_techo = y_base - AMPLITUD_ONDA - 10

    largo_onda = (ANCHO - 2 * margen) / 1.4
    puntos = [(margen, y_techo)]
    x = margen
    while x <= ANCHO - margen:
        y = y_base + AMPLITUD_ONDA * math.sin(2 * math.pi * (x - margen) / largo_onda)
        puntos.append((x, y))
        x += 6
    puntos.append((ANCHO - margen, y_techo))

    # La forma ondulada no se puede rellenar con degradado directamente (PIL
    # no soporta fill de gradiente en rounded_rectangle/polygon): se arma una
    # mascara con la misma silueta y se pega un degradado vertical a traves
    # de ella.
    mascara = Image.new("L", (ANCHO, ALTO), 0)
    draw_mascara = ImageDraw.Draw(mascara)
    draw_mascara.rounded_rectangle([margen, y_techo, ANCHO - margen, ALTO - margen], radius=radio_tarjeta, fill=255)
    draw_mascara.polygon(puntos, fill=0)

    gradiente = _gradiente_vertical(ANCHO, ALTO, ONDA_COLOR_ARRIBA, ONDA_COLOR_ABAJO)
    lienzo.paste(gradiente, (0, 0), mascara)

    # --- Los 3 beneficios sobre la franja de color ---
    y_iconos = y_base + AMPLITUD_ONDA + 130
    icono_caja = 150
    icono_glifo = int(icono_caja * 0.6)
    ancho_col = (ANCHO - 2 * margen) / 3
    fuente_beneficio = _fuente(32)

    for i, (clave, (linea1, linea2)) in enumerate(BENEFICIOS):
        cx = margen + ancho_col * (i + 0.5)
        draw.rounded_rectangle(
            [cx - icono_caja / 2, y_iconos - icono_caja / 2, cx + icono_caja / 2, y_iconos + icono_caja / 2],
            radius=28,
            outline=BLANCO,
            width=4,
        )
        icono = _icono(clave, icono_glifo)
        lienzo.paste(icono, (int(cx - icono_glifo / 2), int(y_iconos - icono_glifo / 2)), icono)

        y_texto = y_iconos + icono_caja / 2 + 35
        y_texto += _texto_centrado(draw, y_texto, linea1, fuente_beneficio, BLANCO, cx=cx) + 12
        _texto_centrado(draw, y_texto, linea2, fuente_beneficio, BLANCO, cx=cx)

    # --- Frase de cierre, con una linea a cada lado en el mismo renglon ---
    y_linea = ALTO - margen - 90
    fuente_cierre = _fuente_regular(30)
    frase = "TU TIEMPO TAMBIEN IMPORTA"
    bbox_frase = draw.textbbox((0, 0), frase, font=fuente_cierre)
    ancho_frase = bbox_frase[2] - bbox_frase[0]
    gap = 30
    x_izq = margen + 100
    x_der = ANCHO - margen - 100
    x_frase = (ANCHO - ancho_frase) / 2
    draw.line([(x_izq, y_linea), (x_frase - gap, y_linea)], fill=BLANCO, width=2)
    draw.line([(x_frase + ancho_frase + gap, y_linea), (x_der, y_linea)], fill=BLANCO, width=2)
    draw.text((x_frase - bbox_frase[0], y_linea - (bbox_frase[3] - bbox_frase[1]) / 2 - bbox_frase[1]), frase, font=fuente_cierre, fill=BLANCO)


def generar_tarjeta_qr(chat_link: str, nombre_negocio: str) -> bytes:
    lienzo = Image.new("RGB", (ANCHO, ALTO), BLANCO)
    draw = ImageDraw.Draw(lienzo)

    margen = 24
    radio_tarjeta = 40

    y = margen + 90

    # --- Logo de la app ---
    logo = Image.open(_LOGO_PATH).convert("RGBA")
    logo_ancho_objetivo = 580
    ratio = logo_ancho_objetivo / logo.width
    logo = logo.resize((logo_ancho_objetivo, int(logo.height * ratio)), Image.LANCZOS)
    lienzo.paste(logo, (int((ANCHO - logo.width) / 2), int(y)), logo)
    y += logo.height + 80

    # --- Titular ---
    x_texto = margen + 110
    draw.text((x_texto, y), "Escanea el QR", font=_fuente(58), fill=CHARCOAL)
    y += draw.textbbox((0, 0), "Escanea el QR", font=_fuente(58))[3] + 20
    draw.text((x_texto, y), "y agenda tu proxima cita", font=_fuente_regular(40), fill=GRIS)
    y += draw.textbbox((0, 0), "y agenda tu proxima cita", font=_fuente_regular(40))[3] + 90

    # --- QR con los colores del branding ---
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=14, border=2)
    qr.add_data(chat_link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=NAVY, back_color=BLANCO).convert("RGB")

    qr_tam = 700
    qr_img = qr_img.resize((qr_tam, qr_tam), Image.NEAREST)

    marco_pad = 26
    draw.rounded_rectangle(
        [
            (ANCHO - qr_tam) / 2 - marco_pad,
            y - marco_pad,
            (ANCHO + qr_tam) / 2 + marco_pad,
            y + qr_tam + marco_pad,
        ],
        radius=28,
        outline=TEAL,
        width=4,
        fill=BLANCO,
    )
    lienzo.paste(qr_img, (int((ANCHO - qr_tam) / 2), int(y)))
    y += qr_tam + marco_pad

    # --- Pastilla con el nombre del negocio, con aire antes de la onda ---
    ancho_util = ANCHO - 2 * margen - 220
    fuente_nombre = _fuente_que_encaja(draw, nombre_negocio.upper(), 46, 26, ancho_util)
    bbox = draw.textbbox((0, 0), nombre_negocio.upper(), font=fuente_nombre)
    ancho_txt, alto_txt = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 46, 26
    pill_ancho = ancho_txt + pad_x * 2
    pill_alto = alto_txt + pad_y * 2
    pill_x0 = (ANCHO - pill_ancho) / 2
    pill_y0 = y + 90

    # El punto mas alto que alcanza la onda es siempre "y_onda_base - AMPLITUD_ONDA"
    # (sin importar la fase): anclar la onda a esa distancia fija del pie de la
    # pastilla garantiza el mismo respiro en toda la tarjeta, no solo en el centro.
    holgura_pastilla_onda = 70
    y_onda_base = pill_y0 + pill_alto + holgura_pastilla_onda + AMPLITUD_ONDA

    _dibujar_onda_con_beneficios(lienzo, draw, margen, radio_tarjeta, y_onda_base)

    draw.rounded_rectangle([pill_x0, pill_y0, pill_x0 + pill_ancho, pill_y0 + pill_alto], radius=pill_alto / 2, fill=TEAL)
    draw.text((pill_x0 + pad_x - bbox[0], pill_y0 + pad_y - bbox[1]), nombre_negocio.upper(), font=fuente_nombre, fill=BLANCO)

    buffer = io.BytesIO()
    lienzo.save(buffer, format="PNG")
    return buffer.getvalue()
