"""Oportunidades a costo fijo por alcance: el vendedor describe el alcance, una IA lo analiza contra el catálogo de automatizaciones y propone
cuáles aplican y su volumen; el requerimiento se envía por correo al especialista de negocio de Invoke, que evalúa la implementación y confirma
el alcance antes de fijar un precio. Con la propuesta aprobada por el cliente se tarifica.
IA: API de Anthropic con la clave guardada en Configuración (archivo solo legible por FaceAdmin, nunca se muestra). Sin clave, un análisis
por palabras clave contra el catálogo (marcado como tal)."""
import json
import os
import re
import unicodedata

import requests
from flask import abort, flash, g, redirect, request, url_for

from app import BD, ahora, app, bitacora, cliente_ok, ej, q1, requiere_login, solo_invoke

IA_ARCH = os.path.join(os.path.dirname(BD), "ia.json")
MODELO = os.environ.get("FACEADMIN_IA_MODELO", "claude-sonnet-5-5")


def ia_config():
    try:
        return json.load(open(IA_ARCH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar(d):
    with open(os.open(IA_ARCH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def especialista():
    return (ia_config().get("especialista") or "cquezada@invoke.cl").strip()


def columnas():
    from app import q
    cols = {r["name"] for r in q("PRAGMA table_info(oportunidades)")}
    for col, t in (("alcance_texto", "TEXT DEFAULT ''"), ("alcance_ia", "TEXT DEFAULT '{}'"), ("alcance_estado", "TEXT DEFAULT ''"),
                   ("alcance_confirmado_por", "TEXT DEFAULT ''"), ("alcance_confirmado", "TEXT DEFAULT ''"), ("alcance_comentario", "TEXT DEFAULT ''"),
                   ("implementacion", "REAL DEFAULT 0")):
        if col not in cols:
            ej(f"ALTER TABLE oportunidades ADD COLUMN {col} {t}")


def _norm(t):
    return unicodedata.normalize("NFKD", (t or "").lower()).encode("ascii", "ignore").decode()


_GEN = {"servi", "equip", "maqui", "faceo", "ejecu", "manua", "horas", "mensu", "diari", "clien", "insta", "confi", "siste", "plata", "crear",
        "nuevo", "nueva", "datos", "centr", "todos", "cada", "desde", "entre", "sobre", "pedid", "segun", "versi"}


def _raices(t):
    return {w[:5] for w in re.findall(r"[a-z0-9]{4,}", _norm(t))} - _GEN


def _heuristico(texto):
    import cobro
    tx = _norm(texto)
    pal = _raices(texto)
    nums = [int(n) for n in re.findall(r"\b(\d{1,5})\s*(?:servidores|equipos|maquinas|vms|nodos|bases|sitios|usuarios)", tx)]
    vol = max(nums) if nums else 5
    cand = []
    for a in cobro.CATALOGO:
        hit = pal & _raices(a["nombre"])
        hit2 = pal & _raices(a.get("por_que", ""))
        sc = 2 * len(hit) + len(hit2)
        if sc >= 2:
            cand.append((sc, {"nombre": a["nombre"], "volumen": vol, "motivo": "coincide con: " + ", ".join(sorted(hit | hit2)[:4])}))
    out = [x for _, x in sorted(cand, key=lambda t: -t[0])][:12]
    return {"origen": "palabras clave (sin IA configurada)", "automatizaciones": out,
            "resumen": f"{len(out)} automatizaciones del catálogo coinciden con el texto. Revisa y ajusta (con la clave de IA el análisis es más preciso).",
            "implementacion_horas": 8 * max(1, len(out)),
            "preguntas": ["¿Cuántos equipos y de qué sistemas operativos entran al alcance?", "¿Qué frecuencia se espera para cada tarea?"]}


def analizar(texto):
    import cobro
    key = ia_config().get("api_key", "")
    if not key:
        return _heuristico(texto)
    cat = [{"nombre": a["nombre"], "categoria": a["categoria"], "creditos": a["creditos"], "unidad": a["unidad"]} for a in cobro.CATALOGO]
    prompt = ("Eres preventa de Invoke (Managed Service sobre Red Hat Ansible Automation Platform). Un vendedor describió el alcance de un cliente. "
              "Elige del CATÁLOGO solo las automatizaciones que cubren ese alcance y estima su volumen mensual en la unidad indicada. "
              "Estima también las horas de implementación (adaptación al cliente) y lista preguntas para el especialista. "
              "Responde SOLO JSON: {\"resumen\": str, \"automatizaciones\": [{\"nombre\": str (exacto del catálogo), \"volumen\": num, \"motivo\": str}], "
              "\"implementacion_horas\": num, \"fuera_de_catalogo\": [str], \"preguntas\": [str]}\n\nCATÁLOGO:\n" + json.dumps(cat, ensure_ascii=False) +
              "\n\nALCANCE DEL VENDEDOR:\n" + texto[:6000])
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": MODELO, "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        txt = "".join(b.get("text", "") for b in r.json().get("content", []))
        d = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except Exception as e:  # noqa: BLE001
        d = _heuristico(texto)
        d["origen"] = f"palabras clave (la IA no respondió: {type(e).__name__})"
        return d
    validos = {a["nombre"] for a in cobro.CATALOGO}
    d["automatizaciones"] = [a for a in d.get("automatizaciones") or [] if a.get("nombre") in validos]
    d["origen"] = f"IA ({MODELO})"
    return d


@app.route("/api/oportunidad/alcance", methods=["POST"])
@requiere_login
def api_alcance():
    solo_invoke()
    texto = (request.form.get("texto") or "").strip()
    if len(texto) < 20:
        return {"error": "Describe el alcance con un poco más de detalle (al menos una o dos frases)"}, 400
    return analizar(texto)


def codigo_empresa(nom):
    """Código único de una empresa nueva, derivado de su nombre (no se edita a mano)."""
    cod = re.sub(r"[^a-z0-9]+", "-", _norm(nom or "")).strip("-")
    pal = [w for w in cod.split("-") if w not in ("sa", "s", "a", "spa", "ltda", "de", "del", "la", "el", "y", "inc", "corp")]
    base = ("-".join(pal[:2]) or cod)[:16]
    codigo, i = base, 2
    while codigo and q1("SELECT id FROM clientes WHERE codigo=?", codigo):
        codigo, i = f"{base}-{i}", i + 1
    return codigo


@app.route("/api/oportunidad/empresa", methods=["POST"])
@requiere_login
def api_empresa():
    """Completa los datos de una empresa nueva a partir de su nombre: código y, con IA, país e industria sugeridos."""
    from contratos import INDUSTRIAS
    solo_invoke()
    nom = (request.form.get("nombre") or "").strip()[:120]
    d = {"codigo": codigo_empresa(nom)}
    key = ia_config().get("api_key", "")
    if key and len(nom) >= 3:
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=30,
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                              json={"model": MODELO, "max_tokens": 300, "messages": [{"role": "user", "content":
                                    "Empresa latinoamericana: «" + nom + "». Si la conoces con certeza razonable, responde SOLO JSON {\"pais\": str, \"industria\": str, "
                                    "\"razon_social\": str}; si no, usa cadenas vacías. País en español (Chile, México, Honduras, Perú, Colombia...). Industria: exactamente una de: " + "; ".join(INDUSTRIAS) + "."}]})
            txt = "".join(b.get("text", "") for b in r.json().get("content", []))
            d.update({k: str(v)[:80] for k, v in json.loads(txt[txt.index("{"):txt.rindex("}") + 1]).items() if k in ("pais", "industria", "razon_social") and v})
            d["ia"] = True
        except Exception:  # noqa: BLE001
            pass
    return d


def avisar_especialista(o, c, d):
    from app import enviar_correo
    autos = "\n".join(f"  · {a['nombre']} — {a.get('volumen')} / mes ({a.get('motivo', '')})" for a in d.get("automatizaciones") or []) or "  (ninguna)"
    enlace = request.url_root.rstrip("/") + url_for("oportunidad", oid=o["id"])
    cuerpo = (f"Nueva oportunidad a costo fijo por alcance para evaluar.\n\nCliente: {c['nombre']} ({c.get('pais') or ''})\nOportunidad: {o['numero']}\n"
              f"Vendedor: {g.usuario['usuario']}\n\nAlcance descrito por el vendedor:\n{o['alcance_texto']}\n\n"
              f"Análisis preliminar ({d.get('origen', '')}):\n{d.get('resumen', '')}\n{autos}\n"
              f"Implementación estimada: {d.get('implementacion_horas', '—')} horas\n"
              + ("Fuera del catálogo: " + "; ".join(d.get("fuera_de_catalogo") or []) + "\n" if d.get("fuera_de_catalogo") else "")
              + ("Preguntas:\n" + "\n".join("  · " + p for p in d.get("preguntas") or []) + "\n" if d.get("preguntas") else "")
              + f"\nEvalúa la implementación y confirma el alcance aquí (sin tu confirmación no se fija precio):\n{enlace}\n\nFaceAdmin · Invoke")
    return enviar_correo(c["id"], especialista(), f"Evaluar alcance · {o['numero']} · {c['nombre']}", cuerpo)


@app.route("/oportunidades/<int:oid>/alcance", methods=["POST"])
@requiere_login
def oportunidad_alcance(oid):
    solo_invoke()
    columnas()
    o = q1("SELECT * FROM oportunidades WHERE id=?", oid) or abort(404)
    c = cliente_ok(o["cliente_id"])
    f = request.form
    if f.get("accion") == "confirmar":
        import cobro
        imp = max(0, cobro._n(f.get("implementacion") or "", 0))
        ej("UPDATE oportunidades SET alcance_estado='confirmado', alcance_confirmado_por=?, alcance_confirmado=?, alcance_comentario=?, implementacion=? WHERE id=?",
           g.usuario["usuario"], ahora(), (f.get("comentario") or "").strip()[:2000], imp, oid)
        bitacora("alcance confirmado por especialista", c["codigo"], {"oportunidad": o["numero"], "implementacion": imp})
        flash("Alcance confirmado: ya se puede preparar la propuesta con precio para que el cliente la apruebe", "ok")
    elif f.get("accion") == "reenviar":
        try:
            d = json.loads(o.get("alcance_ia") or "{}")
        except ValueError:
            d = {}
        est = avisar_especialista(o, c, d)
        flash(f"Correo al especialista ({especialista()}): {est}", "ok" if est == "enviado" else "error")
    return redirect(url_for("oportunidad", oid=oid))


@app.route("/config/ia", methods=["POST"])
@requiere_login
def config_ia():
    solo_invoke()
    d = ia_config()
    esp = (request.form.get("especialista") or "").strip()
    if esp and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", esp):
        flash("El correo del especialista no es válido", "error")
        return redirect(url_for("config_correo"))
    d["especialista"] = esp or d.get("especialista", "")
    k = (request.form.get("api_key") or "").strip()
    if k:
        d["api_key"] = k
    if request.form.get("quitar_clave"):
        d.pop("api_key", None)
    _guardar(d)
    bitacora("configuración de IA y especialista", "invoke", {"especialista": d.get("especialista"), "ia": bool(d.get("api_key"))})
    flash("Configuración guardada", "ok")
    return redirect(url_for("config_correo"))
