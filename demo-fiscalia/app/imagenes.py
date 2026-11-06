"""Análisis de las imágenes adjuntas a una denuncia, liviano y en CPU (segundos): texto visible (OCR: placas, letreros, fechas en
cámaras de vigilancia), metadatos de la foto (EXIF: fecha de captura, equipo, GPS) y calidad; y la coherencia con el relato y la
fecha de los hechos. Con GPU (OpenShift AI) se suma un modelo de visión que describe la escena."""
import base64
import datetime as dt
import io
import re

import numpy as np
from PIL import ExifTags, Image

_OCR = None


def _ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def _norm(t):
    return re.sub(r"[^A-Z0-9]", "", (t or "").upper())


def _fechas(texto):
    out = []
    for m in re.finditer(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})|(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})", texto or ""):
        try:
            y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m.group(1) else (int(m.group(6)), int(m.group(5)), int(m.group(4)))
            out.append(dt.datetime(y, mo, d))
        except ValueError:
            pass
    return out


def analizar(b64, relato="", tipo="", fecha_hechos=""):
    datos = base64.b64decode(b64.split(",")[-1])
    im = Image.open(io.BytesIO(datos))
    exif = {}
    try:
        for k, v in (im.getexif() or {}).items():
            exif[ExifTags.TAGS.get(k, k)] = v
        sub = im.getexif().get_ifd(0x8769) if hasattr(im.getexif(), "get_ifd") else {}
        for k, v in (sub or {}).items():
            exif[ExifTags.TAGS.get(k, k)] = v
    except Exception:  # noqa: BLE001
        pass
    gris = np.asarray(im.convert("L").resize((min(im.width, 640), int(im.height * min(im.width, 640) / im.width))), dtype=float)
    nitidez = float(np.var(gris[:-2, 1:-1] + gris[2:, 1:-1] + gris[1:-1, :-2] + gris[1:-1, 2:] - 4 * gris[1:-1, 1:-1]))
    res, _ = _ocr()(np.asarray(im.convert("RGB")))
    textos = [r[1] for r in (res or []) if float(r[2]) > .5]
    unido = " ".join(textos)
    # coherencia con el relato: texto de la imagen que aparece en el relato (placas, nombres, números) y fecha de la imagen frente a la de los hechos
    rel = _norm(relato)
    tokens = {_norm(t) for t in re.findall(r"[A-Za-z0-9-]{4,}", unido) if len(_norm(t)) >= 4}
    seguidos = _norm("".join(textos))   # el OCR a veces parte una placa en dos trozos: se busca también en el texto continuo
    coincide = [t for t in tokens if t in rel] or [seguidos[i:i + 6] for i in range(0, max(0, len(seguidos) - 5)) if seguidos[i:i + 6] in rel][:1]
    f_img = _fechas(unido) + _fechas(str(exif.get("DateTimeOriginal", "")).replace(":", "-", 2))
    try:
        f_h = dt.datetime.fromisoformat(fecha_hechos[:16]) if fecha_hechos else None
    except ValueError:
        f_h = None
    motivos, relacion = [], "indeterminado"
    if coincide:
        relacion = "coherente"
        motivos.append(f"el texto de la imagen ({', '.join(coincide[:2])}) aparece en el relato")
    if f_img and f_h:
        dif = min(abs((f - f_h).days) for f in f_img)
        if dif <= 2:
            relacion = "coherente" if relacion != "no coherente" else relacion
            motivos.append(f"la fecha de la imagen ({f_img[0]:%Y-%m-%d}) coincide con la de los hechos")
        elif dif > 30:
            relacion = "no coherente"
            motivos.append(f"la fecha de la imagen ({f_img[0]:%Y-%m-%d}) está a {dif} días de los hechos")
    if nitidez < 15:
        motivos.append("imagen poco nítida")
    if not motivos:
        motivos.append("no hay texto ni fecha en la imagen para contrastar con el relato: requiere revisión humana")
    meta = []
    if exif.get("DateTimeOriginal"):
        meta.append(f"tomada {exif['DateTimeOriginal']}")
    if exif.get("Model"):
        meta.append(f"equipo {exif.get('Make', '')} {exif['Model']}".strip())
    if exif.get("GPSInfo"):
        meta.append("con ubicación GPS")
    desc = f"Imagen {im.width}×{im.height}" + (f" · {', '.join(meta)}" if meta else " · sin metadatos EXIF") + \
           (f" · texto detectado: {unido[:120]}" if textos else " · sin texto legible") + f" · nitidez {'buena' if nitidez >= 60 else ('media' if nitidez >= 15 else 'baja')}"
    return {"descripcion": desc, "elementos": textos[:10], "texto_visible": unido[:200], "relacion": relacion, "observacion": "; ".join(motivos)}
