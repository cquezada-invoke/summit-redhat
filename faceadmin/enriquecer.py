"""Agente de IA que completa los datos de una empresa buscando en internet (herramienta de búsqueda web del servidor de la API de Anthropic):
razón social, identificador fiscal del país (RUT, RFC, NIT, RUC…), país, industria, sitio web, dirección, tamaño y una descripción breve, con
las fuentes. Nada inventado: lo que no encuentra con certeza queda vacío. Se usa al escribir una empresa nueva (oportunidad o contrato), en la
ficha de la cuenta («Completar con IA», con confirmación) y en segundo plano al crear la oportunidad o el contrato (solo llena campos vacíos)."""
import json
import threading

import requests
from flask import abort, g, request

from app import ahora, app, bitacora, cliente_ok, ej, q, q1, requiere_login, solo_invoke

CAMPOS = ("razon_social", "rut", "pais", "industria", "sitio_web", "direccion", "empleados", "descripcion")
EXTRA = ("sitio_web", "direccion", "empleados", "descripcion", "ia_fuentes", "ia_completado")
SIN_CLAVE = ("Falta configurar la clave de IA: Configuración › Correo, sección «Alcance de oportunidades: especialista e IA» "
             "(clave de la API de Anthropic).")
HERRAMIENTA = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}


def columnas():
    cols = {r["name"] for r in q("PRAGMA table_info(clientes)")}
    for col in EXTRA:
        if col not in cols:
            ej(f"ALTER TABLE clientes ADD COLUMN {col} TEXT DEFAULT ''")


with app.app_context():
    columnas()


def _fuentes_ok(t):
    try:
        v = json.loads(t or "[]")
    except ValueError:
        return []
    return [u for u in v if isinstance(u, str) and u.startswith(("https://", "http://"))][:8] if isinstance(v, list) else []


app.jinja_env.filters["fromjson"] = _fuentes_ok


def _instrucciones(nombre, pais):
    from contratos import INDUSTRIAS, PAISES
    return ("Eres un agente que investiga empresas para un CRM. Busca en internet la empresa «" + nombre + "»"
            + (" de " + pais if pais else " (probablemente latinoamericana)") + " y responde AL FINAL SOLO un objeto JSON con estas claves:\n"
            "razon_social (nombre legal completo), rut (identificador fiscal del país: RUT en Chile, RFC en México, NIT en Colombia, RUC en Perú, etc., "
            "con su formato oficial), pais (uno de: " + ", ".join(PAISES) + "), industria (exactamente una de: " + "; ".join(INDUSTRIAS) + "), "
            "sitio_web (URL oficial), direccion (casa matriz), empleados (cantidad aproximada, p. ej. «~10.000»), descripcion (1-2 frases en español), "
            "fuentes (lista de URLs consultadas que respaldan los datos).\n"
            "Reglas: no inventes nada. Si un dato no aparece con certeza en fuentes confiables (sitio oficial, registro público, regulador, prensa "
            "seria), déjalo como cadena vacía. Si hay varias empresas con ese nombre, elige la que coincide con el país; si no es posible "
            "distinguir, deja vacíos los datos dudosos.")


def _json(txt):
    try:
        return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except ValueError:
        return {}


def _llamar(key, mensajes):
    from alcance import MODELO
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": MODELO, "max_tokens": 2500, "tools": [HERRAMIENTA], "messages": mensajes})
    d = r.json()
    from alcance import _ia_registrar
    _ia_registrar("enriquecer", r.status_code == 200)
    if r.status_code != 200:
        raise RuntimeError((d.get("error") or {}).get("message") or f"HTTP {r.status_code}")
    return d


def buscar(nombre, pais=""):
    """{ok, datos{CAMPOS}, fuentes[], error?, sin_clave?}. Nunca lanza excepción."""
    from alcance import ia_config
    from contratos import INDUSTRIAS, PAISES
    nombre = (nombre or "").strip()[:120]
    if len(nombre) < 2:
        return {"ok": False, "error": "Indica el nombre de la empresa"}
    key = ia_config().get("api_key", "")
    if not key:
        return {"ok": False, "sin_clave": True, "error": SIN_CLAVE}
    msgs = [{"role": "user", "content": _instrucciones(nombre, (pais or "").strip())}]
    try:
        for _ in range(4):   # la API puede pausar una búsqueda larga (pause_turn): se reenvía tal cual para continuar
            d = _llamar(key, msgs)
            if d.get("stop_reason") != "pause_turn":
                break
            msgs = msgs[:1] + [{"role": "assistant", "content": d["content"]}]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "No se pudo consultar la IA: " + str(e)[:200]}
    bloques = d.get("content") or []
    txt = "".join(b.get("text", "") for b in bloques if b.get("type") == "text")
    j = _json(txt)
    datos = {k: str(j.get(k) or "").strip()[:300 if k == "descripcion" else 160] for k in CAMPOS}
    if datos["pais"] and datos["pais"] not in PAISES:
        datos["pais"] = next((p for p in PAISES if p.lower() == datos["pais"].lower()), "")
    if datos["industria"] and datos["industria"] not in INDUSTRIAS:
        datos["industria"] = ""
    fuentes = [u for u in (j.get("fuentes") or []) if isinstance(u, str) and u.startswith("http")]
    for b in bloques:   # además, lo citado por la búsqueda
        for c in b.get("citations") or []:
            if c.get("url", "").startswith("http"):
                fuentes.append(c["url"])
    fuentes = list(dict.fromkeys(fuentes))[:8]
    if not any(datos.values()):
        return {"ok": True, "datos": datos, "fuentes": fuentes, "vacio": True}
    return {"ok": True, "datos": datos, "fuentes": fuentes}


def guardar_vacios(cid, datos, fuentes, por="IA"):
    """Llena solo los campos vacíos de la cuenta; devuelve los campos completados."""
    c = q1("SELECT * FROM clientes WHERE id=?", cid)
    if not c:
        return []
    hechos = [k for k in CAMPOS if (datos.get(k) or "").strip() and not (c.get(k) or "").strip()]
    for k in hechos:
        ej(f"UPDATE clientes SET {k}=? WHERE id=?", datos[k].strip(), cid)
    if hechos:
        ej("UPDATE clientes SET ia_fuentes=?, ia_completado=? WHERE id=?", json.dumps(fuentes), ahora() + " · " + por, cid)
    return hechos


def guardar_extra(cid, f):
    """Campos adicionales ingresados en el formulario de una empresa (se guardan si la cuenta los tiene vacíos)."""
    datos = {k: (f.get(k) or "").strip() for k in CAMPOS}
    guardar_vacios(cid, datos, _fuentes_ok(f.get("ia_fuentes")), "formulario")


def completar_fondo(cid):
    """Completa en segundo plano los campos vacíos de la cuenta (no bloquea la creación)."""
    from alcance import ia_config
    c = q1("SELECT * FROM clientes WHERE id=?", cid)
    if not c or not ia_config().get("api_key") or all((c.get(k) or "").strip() for k in CAMPOS):
        return False
    usuario = getattr(g, "usuario", None) or {}

    def tarea():
        with app.app_context():
            g.usuario = usuario
            r = buscar(c["nombre"], c.get("pais") or "")
            if r.get("ok"):
                hechos = guardar_vacios(cid, r["datos"], r["fuentes"], "segundo plano")
                if hechos:
                    try:
                        bitacora("datos de la cuenta completados con IA", c["codigo"], {"campos": hechos, "fuentes": r["fuentes"]})
                    except Exception:  # noqa: BLE001
                        pass
    threading.Thread(target=tarea, daemon=True).start()
    return True


@app.route("/api/empresa/enriquecer", methods=["POST"])
@requiere_login
def api_enriquecer():
    solo_invoke()
    nom, pais = request.form.get("nombre") or "", request.form.get("pais") or ""
    if (request.form.get("cid") or "").isdigit():
        c = cliente_ok(int(request.form["cid"])) or abort(404)
        nom, pais = nom or c["nombre"], pais or (c.get("pais") or "")
    r = buscar(nom, pais)
    return r, (200 if r.get("ok") or r.get("sin_clave") else 502)
