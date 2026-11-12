"""Seguridad › Incidentes de seguridad: flujo propio de gestión de incidentes de seguridad con el SIEM del cliente (Wazuh).

Cómo funciona (nada se simula):
· SIEM: plataforma «SIEM (Wazuh)» (skill «wazuh») que instala «SIEM · Instalar Wazuh» en una máquina del inventario. Sus
  claves, la CA de su receptor TLS y el token de la integración viven en la bóveda (web/<máquina>/wazuh); FaceOps los lee de
  ahí y nunca los muestra.
· Logs hacia el SIEM (agentless): «SIEM · Enviar logs al SIEM» (rsyslog/journald por TLS 6514, con ticket y aprobación),
  «SIEM · Enviar logs de AAP» (logging externo de AAP) y la auditoría de FaceOps (tickets, aprobaciones, ingresos y cambios),
  que este módulo envía por TLS al mismo receptor. El agente de Wazuh es una opción de la acción, no va por defecto.
· SIEM hacia FaceOps: la integración «custom-faceops» de Wazuh manda las alertas de nivel >= N a /api/siem/alerta/<siem>
  (token = sha256 del token de la bóveda). Cada alerta abre un incidente o, si ya hay uno abierto de la misma regla, máquina
  e IP en la última hora, se suma como evidencia.
· Incidente: estados nuevo → triage → contención → erradicación → recuperación → cerrado; severidad (por nivel de la regla),
  responsable (grupo Seguridad, datos/seguridad-incidentes.yml), evidencias (el evento del SIEM con enlace a su consola),
  acciones de contención sugeridas que crean un ticket con aprobación («Seguridad · Contención de incidente») y línea de
  tiempo. Al abrirse se avisa por correo al responsable."""
import fcntl
import hashlib
import json
import os
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
import yaml
from flask import abort, flash, g, redirect, render_template, request, url_for

from . import requiere_login
from .bd import ahora, ajuste, auditar, con, ej, j, q, q1
from .vistas import _escritura, bp, equipos_visibles

AUTO_INSTALAR = "SIEM · Instalar Wazuh"
AUTO_LOGS = "SIEM · Enviar logs al SIEM"
AUTO_AAP = "SIEM · Enviar logs de AAP"
AUTO_CONTENCION = "Seguridad · Contención de incidente"
SKILL = "wazuh"
ANFITRION = "192.168.122.1"   # AAP y FaceOps: nunca se bloquean ni quedan fuera de una máquina aislada
CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "seguridad-incidentes.yml")
ESTADOS = [("nuevo", "Nuevo"), ("triage", "Triage"), ("contencion", "Contención"), ("erradicacion", "Erradicación"),
           ("recuperacion", "Recuperación"), ("cerrado", "Cerrado")]
NOMBRE_ESTADO = dict(ESTADOS)
ORDEN = [k for k, _ in ESTADOS]
SEVERIDADES = [("critica", "Crítica", 12), ("alta", "Alta", 10), ("media", "Media", 7), ("baja", "Baja", 0)]
CONTENCION = {"bloquear_ip": "Bloquear la IP de origen", "aislar": "Aislar la máquina en el firewall",
              "deshabilitar_usuario": "Deshabilitar el usuario", "revertir": "Quitar el bloqueo y el aislamiento",
              "habilitar_usuario": "Volver a habilitar el usuario"}
TICKET_ABIERTO = ("pendiente_aprobacion", "en_cola", "en_ejecucion")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS seg_incidentes (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, numero TEXT NOT NULL, titulo TEXT NOT NULL, severidad TEXT DEFAULT 'media',
  estado TEXT DEFAULT 'nuevo', responsable_grupo TEXT DEFAULT '', responsable_correo TEXT DEFAULT '', responsable_usuario TEXT DEFAULT '',
  siem TEXT DEFAULT '', regla_id TEXT DEFAULT '', nivel INTEGER, equipo TEXT DEFAULT '', equipo_id INTEGER, ip_origen TEXT DEFAULT '',
  usuario TEXT DEFAULT '', mitre TEXT DEFAULT '[]', alertas INTEGER DEFAULT 1, creado TEXT, actualizado TEXT, cerrado TEXT,
  cierre TEXT DEFAULT '', correo TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS seg_incidentes_c ON seg_incidentes(cliente_id, estado, regla_id);
CREATE TABLE IF NOT EXISTS seg_incidentes_linea (
  id INTEGER PRIMARY KEY, incidente_id INTEGER NOT NULL, cuando TEXT, quien TEXT, tipo TEXT, texto TEXT, ticket_id INTEGER,
  evento TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS seg_incidentes_linea_i ON seg_incidentes_linea(incidente_id, cuando);
"""
_listo = {"v": False}


def _tabla():
    if not _listo["v"]:
        con().executescript(ESQUEMA)
        _listo["v"] = True


def conf(cliente):
    """Responsable por cliente (datos/seguridad-incidentes.yml): {grupo, correo, nivel}."""
    try:
        d = yaml.safe_load(open(CONF, encoding="utf-8")) or {}
    except OSError:
        d = {}
    base = d.get("defecto") or {}
    return dict(base, **((d.get("clientes") or {}).get((cliente or {}).get("codigo") or "", {}) or {}))


def severidad(nivel):
    return next(k for k, _, m in SEVERIDADES if int(nivel or 0) >= m)


# ── SIEM: plataforma, credenciales (bóveda) e índice ─────────────────────────────────────────────────────────────────
def siems(eqs=None):
    from .inventario import skills_de
    return [e for e in (eqs if eqs is not None else equipos_visibles()) if SKILL in skills_de(e) and e.get("estado") != "retirado"]


_cred_cache = {}


def cred(nombre):
    """Credenciales del SIEM en la bóveda (60 s en memoria). Nunca se muestran."""
    from .boveda import leer
    c = _cred_cache.get(nombre)
    if c and time.time() - c[0] < 60:
        return c[1]
    v = leer(f"web/{nombre}/wazuh") or {}
    _cred_cache[nombre] = (time.time(), v)
    return v


def _indexer(siem, ruta, cuerpo=None, espera=8):
    c = cred(siem["nombre"])
    ip = c.get("ip") or siem.get("ip")
    if not (c.get("clave") and ip):
        return None
    try:
        r = requests.request("POST" if cuerpo is not None else "GET", f"https://{ip}:9200/{ruta}", json=cuerpo, verify=False,
                             auth=(c.get("usuario") or "admin", c["clave"]), timeout=espera)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def salud_siem(siem):
    """Estado del SIEM: índice (salud del clúster), servicios recolectados y alertas de las últimas 24 h."""
    h = _indexer(siem, "_cluster/health") or {}
    serv = " ".join(j(siem.get("servicios"), []) if isinstance(siem.get("servicios"), str) else (siem.get("servicios") or []))
    faltan = [s for s in ("wazuh-manager", "wazuh-indexer", "wazuh-dashboard") if serv and s not in serv]
    n24 = (_indexer(siem, "wazuh-alerts-*/_count", {"query": {"range": {"timestamp": {"gte": "now-24h"}}}}) or {}).get("count")
    color = h.get("status")
    pct = 100 if color == "green" and not faltan else 75 if color == "yellow" and not faltan else 40 if color else 0
    return {"indice": color or "sin respuesta", "faltan": faltan, "alertas_24h": n24, "pct": pct, "version": cred(siem["nombre"]).get("version")}


def fuentes_vistas(siem):
    """Última alerta recibida por cada fuente (máquina por syslog, agente, FaceOps o AAP) en 7 días."""
    r = _indexer(siem, "wazuh-alerts-*/_search", {"size": 0, "query": {"range": {"timestamp": {"gte": "now-7d"}}},
                                                  "aggs": {"h": {"terms": {"field": "predecoder.hostname", "size": 100}, "aggs": {"u": {"max": {"field": "timestamp"}}}},
                                                           "g": {"terms": {"field": "rule.groups", "include": ["faceops_auditoria", "faceops_aap"], "size": 5},
                                                                 "aggs": {"u": {"max": {"field": "timestamp"}}}}}}) or {}
    ag = r.get("aggregations") or {}
    out = {b["key"]: b["u"].get("value_as_string") for b in (ag.get("h") or {}).get("buckets", [])}
    out.update({"faceops" if b["key"] == "faceops_auditoria" else "aap": b["u"].get("value_as_string") for b in (ag.get("g") or {}).get("buckets", [])})
    return out


def url_consola(siem):
    return url_for("consola.consola_web_plataforma", eid=siem["id"], cod=SKILL, sub="")


def url_evento(siem, alerta_id):
    base = url_consola(siem).rstrip("/")
    return (f"{base}/app/data-explorer/discover#?_g=(time:(from:now-30d,to:now))&_a=(metadata:(indexPattern:'wazuh-alerts-*',view:discover))"
            f"&_q=(query:(language:kuery,query:'id:\"{alerta_id}\"'))")


# ── Alertas del SIEM → incidentes ────────────────────────────────────────────────────────────────────────────────────
def _numero(cliente_id):
    n = (q1("SELECT COUNT(*) n FROM seg_incidentes WHERE cliente_id=?", cliente_id) or {}).get("n", 0) + 1
    return f"SI-{n:04d}"


def linea(iid, quien, tipo, texto, ticket_id=None, evento=None):
    ej("INSERT INTO seg_incidentes_linea(incidente_id, cuando, quien, tipo, texto, ticket_id, evento) VALUES (?,?,?,?,?,?,?)",
       iid, ahora(), quien, tipo, texto, ticket_id, json.dumps(evento, ensure_ascii=False) if evento else "")
    ej("UPDATE seg_incidentes SET actualizado=? WHERE id=?", ahora(), iid)


def recibir(siem_eq, alerta):
    """Una alerta de Wazuh → incidente nuevo o evidencia de uno abierto. Devuelve (incidente, nuevo)."""
    _tabla()
    regla = alerta.get("rule") or {}
    data = alerta.get("data") or {}
    agente = alerta.get("agent") or {}
    pre = alerta.get("predecoder") or {}
    host = pre.get("hostname") if agente.get("id") in (None, "000") else agente.get("name")
    host = (host or agente.get("name") or "").strip()
    cid = siem_eq["cliente_id"]
    e = q1("SELECT id, nombre FROM equipos WHERE cliente_id=? AND estado!='retirado' AND (lower(nombre)=lower(?) OR lower(nombre)=lower(?)) ORDER BY id LIMIT 1",
           cid, host, host.split(".")[0])
    ip = (data.get("srcip") or "").strip()
    usuario = (data.get("dstuser") or data.get("srcuser") or "").strip()
    nivel = int(regla.get("level") or 0)
    ev = {"id": alerta.get("id"), "timestamp": alerta.get("timestamp"), "regla": regla.get("id"), "nivel": nivel,
          "descripcion": regla.get("description"), "host": host, "srcip": ip, "usuario": usuario,
          "full_log": (alerta.get("full_log") or "")[:600], "mitre": (regla.get("mitre") or {}).get("technique") or []}
    hace = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    ab = q1("""SELECT * FROM seg_incidentes WHERE cliente_id=? AND estado!='cerrado' AND regla_id=? AND equipo=? AND ip_origen=?
               AND actualizado>=? ORDER BY id DESC LIMIT 1""", cid, str(regla.get("id") or ""), e["nombre"] if e else host, ip, hace)
    if ab:
        ej("UPDATE seg_incidentes SET alertas=alertas+1, nivel=MAX(nivel, ?) WHERE id=?", nivel, ab["id"])
        linea(ab["id"], "SIEM", "evidencia", f"Nueva alerta de la misma regla: {regla.get('description')}", evento=ev)
        return q1("SELECT * FROM seg_incidentes WHERE id=?", ab["id"]), False
    cl = q1("SELECT * FROM clientes WHERE id=?", cid)
    cf = conf(cl)
    sev = severidad(nivel)
    titulo = f"{regla.get('description') or 'Alerta del SIEM'} en {e['nombre'] if e else host or 'origen desconocido'}"
    iid = ej("""INSERT INTO seg_incidentes(cliente_id, numero, titulo, severidad, estado, responsable_grupo, responsable_correo, siem,
                regla_id, nivel, equipo, equipo_id, ip_origen, usuario, mitre, creado, actualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             cid, _numero(cid), titulo[:240], sev, "nuevo", cf.get("grupo") or "Seguridad", cf.get("correo") or "", siem_eq["nombre"],
             str(regla.get("id") or ""), nivel, e["nombre"] if e else host, e["id"] if e else None, ip, usuario,
             json.dumps(ev["mitre"]), ahora(), ahora())
    linea(iid, "SIEM", "apertura", f"Alerta de {siem_eq['nombre']} (regla {regla.get('id')}, nivel {nivel}): {regla.get('description')}", evento=ev)
    inc = q1("SELECT * FROM seg_incidentes WHERE id=?", iid)
    aviso = _correo(inc, cl, ev)
    ej("UPDATE seg_incidentes SET correo=? WHERE id=?", aviso, iid)
    linea(iid, "FaceOps", "aviso", f"Correo al responsable ({inc['responsable_grupo']}): {aviso}")
    auditar("SIEM", cid, "incidente de seguridad", inc["numero"], {"regla": regla.get("id"), "nivel": nivel, "equipo": inc["equipo"], "ip": ip})
    return q1("SELECT * FROM seg_incidentes WHERE id=?", iid), True


def _correo(inc, cl, ev):
    from .correo import enviar, hora
    para = inc["responsable_correo"]
    pub = os.environ.get("FACEOPS_URL_PUBLICA", "https://faceops.34.176.42.56.sslip.io:8843/faceops").rstrip("/")
    cuerpo = (f"Incidente de seguridad {inc['numero']} · severidad {inc['severidad']}\n\n{inc['titulo']}\n"
              f"Máquina: {inc['equipo'] or '—'} · IP de origen: {inc['ip_origen'] or '—'} · usuario: {inc['usuario'] or '—'}\n"
              f"Detectado: {hora(ev.get('timestamp') or inc['creado'], para)}\n\n"
              f"Revísalo y aplica la contención en FaceOps: {pub}/{(cl.get('codigo') or '').lower()}/seguridad/incidentes-seguridad/{inc['id']}\n")
    try:
        return enviar(para, f"[FaceOps] {inc['numero']} · {inc['severidad'].capitalize()} · {inc['titulo'][:90]}", cuerpo)
    except Exception as x:  # noqa: BLE001  el incidente queda abierto aunque el correo falle
        return f"no enviado: {type(x).__name__}"


@bp.route("/api/siem/alerta/<nombre>", methods=["POST"])
def api_siem_alerta(nombre):
    """Integración «custom-faceops» de Wazuh. Token: sha256 del token del SIEM en la bóveda (web/<siem>/wazuh); sin él, 401."""
    c = cred(nombre)
    esperado = hashlib.sha256((c.get("token") or "").encode()).hexdigest() if c.get("token") else None
    if not esperado or request.headers.get("Authorization", "") != f"Bearer {esperado}":
        return {"error": "no autorizado"}, 401
    e = q1("SELECT * FROM equipos WHERE nombre=? AND estado!='retirado' ORDER BY id LIMIT 1", nombre)
    if not e:
        return {"error": "SIEM desconocido"}, 404
    alerta = request.get_json(silent=True) or {}
    if not (alerta.get("rule") or {}).get("id"):
        return {"error": "alerta sin regla"}, 400
    inc, nuevo = recibir(e, alerta)
    return {"ok": True, "incidente": inc["numero"], "nuevo": nuevo}


# ── Páginas ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _auto(nombre):
    return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], nombre)


def _tickets_de(nombre, limite=200):
    a = _auto(nombre)
    if not a:
        return []
    return q("SELECT * FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT ?", g.cliente["id"], a["id"], limite)


def fuentes(siem, eqs):
    """Qué envía logs a este SIEM: máquinas (último ticket exitoso de «Enviar logs al SIEM»), AAP y la auditoría de FaceOps."""
    maquinas, aap_ = {}, None
    for t in reversed(_tickets_de(AUTO_LOGS)):
        p = j(t["parametros"], {})
        if p.get("sl_siem_nombre") and p["sl_siem_nombre"] != siem["nombre"]:
            continue
        for n in (t["limite"] or "").split(":"):
            if n:
                maquinas.setdefault(n, {})
                if t["estado"] in TICKET_ABIERTO:
                    maquinas[n]["en_curso"] = t
                if t["estado"] == "exitoso":
                    maquinas[n].update(modo=p.get("sl_modo", "activar"), agente=str(p.get("sl_agente", "")).lower() in ("true", "1", "si", "sí"), ticket=t)
    for t in reversed(_tickets_de(AUTO_AAP)):
        if t["estado"] == "exitoso":
            aap_ = dict(modo=j(t["parametros"], {}).get("sl_modo", "activar"), ticket=t)
    fo = (j(ajuste("siem_auditoria") or "", {}) or {})
    return {"maquinas": maquinas, "aap": aap_, "faceops": fo if fo.get("cliente_id") == g.cliente["id"] and fo.get("siem") == siem["nombre"] else None}


@bp.route("/seguridad/incidentes-seguridad")
@requiere_login
def seg_incidentes():
    _tabla()
    from .inventario import skills_de, sistema_de
    eqs = equipos_visibles()
    ss = siems(eqs)
    est = request.args.get("estado", "abiertos")
    filas = q("SELECT * FROM seg_incidentes WHERE cliente_id=? ORDER BY id DESC LIMIT 300", g.cliente["id"])
    if est == "abiertos":
        filas = [x for x in filas if x["estado"] != "cerrado"]
    elif est in NOMBRE_ESTADO:
        filas = [x for x in filas if x["estado"] == est]
    todos = q("SELECT estado, severidad FROM seg_incidentes WHERE cliente_id=?", g.cliente["id"])
    cuenta = {k: sum(1 for x in todos if x["estado"] == k) for k in ORDEN}
    info = []
    for s in ss:
        fu = fuentes(s, eqs)
        vistos = fuentes_vistas(s)
        info.append({"e": s, "salud": salud_siem(s), "consola": url_consola(s), "fuentes": fu, "vistos": vistos})
    candidatos_siem = [e for e in eqs if sistema_de(e) == "linux" and SKILL not in skills_de(e) and "rhel" in (e.get("so") or "").lower()
                       and e.get("estado") != "retirado"]
    linux = [e for e in eqs if sistema_de(e) == "linux" and SKILL not in skills_de(e) and e.get("estado") != "retirado"]
    autos = {k: _auto(k) for k in (AUTO_INSTALAR, AUTO_LOGS, AUTO_AAP, AUTO_CONTENCION)}
    from .pruebas import estado as est_prueba
    bloqueo = {k: (None if not a else (None if est_prueba(dict(a, cliente_id=g.cliente["id"]))["estado"] == "certificada" else "sin prueba aprobada"))
               for k, a in autos.items()}
    return render_template("seg_incidentes.html", filas=filas, est=est, cuenta=cuenta, ESTADOS=ESTADOS, siems=info,
                           candidatos_siem=candidatos_siem, linux=linux, autos=autos, bloqueo=bloqueo, cf=conf(g.cliente),
                           escribe=g.perfil["modo"] == "escritura")


def _incidente(iid):
    _tabla()
    x = q1("SELECT * FROM seg_incidentes WHERE id=? AND cliente_id=?", iid, g.cliente["id"])
    if not x:
        abort(404)
    return x


def sugerencias(x, eqs=None):
    """Acciones de contención que aplican a este incidente, con su motivo cuando no se pueden pedir."""
    e = next((y for y in (eqs or equipos_visibles()) if y["id"] == x["equipo_id"]), None) if x["equipo_id"] else None
    s = []
    sin_e = None if e else "la máquina afectada no está en el inventario de FaceOps"
    if x["ip_origen"]:
        prot = x["ip_origen"] == ANFITRION or any(x["ip_origen"] == (z.get("ip") or "") for z in siems(eqs) if z)
        s.append({"accion": "bloquear_ip", "t": f"Bloquear {x['ip_origen']} en {x['equipo'] or 'la máquina'}",
                  "no": sin_e or ("es la IP de AAP/FaceOps o del SIEM: no se bloquea" if prot else None)})
    s.append({"accion": "aislar", "t": f"Aislar {x['equipo'] or 'la máquina'} (solo AAP y el SIEM la alcanzan)", "no": sin_e})
    if x["usuario"]:
        s.append({"accion": "deshabilitar_usuario", "t": f"Deshabilitar el usuario {x['usuario']}",
                  "no": sin_e or ("cuenta protegida (root o de servicio)" if x["usuario"] in ("root", "ansible", "faceops", "aap-kvm") else None)})
    s.append({"accion": "revertir", "t": "Quitar el bloqueo y el aislamiento (recuperación)", "no": sin_e})
    if x["usuario"]:
        s.append({"accion": "habilitar_usuario", "t": f"Volver a habilitar {x['usuario']} (recuperación)", "no": sin_e})
    return e, s


@bp.route("/seguridad/incidentes-seguridad/<int:iid>")
@requiere_login
def seg_incidente(iid):
    x = _incidente(iid)
    x["mitre_l"] = j(x["mitre"], []) or []
    eqs = equipos_visibles()
    e, sug = sugerencias(x, eqs)
    lin = q("""SELECT l.*, t.numero ticket_numero, t.estado ticket_estado FROM seg_incidentes_linea l LEFT JOIN tickets t ON t.id=l.ticket_id
               WHERE l.incidente_id=? ORDER BY l.id""", iid)
    for l_ in lin:
        l_["ev"] = j(l_["evento"], None)
    siem = next((s for s in siems(eqs) if s["nombre"] == x["siem"]), None)
    pos = ORDEN.index(x["estado"]) if x["estado"] in ORDEN else 0
    a = _auto(AUTO_CONTENCION)
    from .pruebas import estado as est_prueba
    bloq = None if a and est_prueba(dict(a, cliente_id=g.cliente["id"]))["estado"] == "certificada" else ("falta en el catálogo" if not a else "sin prueba aprobada")
    return render_template("seg_incidente.html", x=x, e=e, sug=sug, lin=lin, ESTADOS=ESTADOS, pos=pos, siem=siem,
                           url_evento=(lambda aid: url_evento(siem, aid)) if siem else None, bloq=bloq,
                           siguiente=ORDEN[pos + 1] if pos + 1 < len(ORDEN) else None, escribe=g.perfil["modo"] == "escritura",
                           NOMBRE_ESTADO=NOMBRE_ESTADO, CONTENCION=CONTENCION)


@bp.route("/seguridad/incidentes-seguridad/<int:iid>/estado", methods=["POST"])
@requiere_login
def seg_incidente_estado(iid):
    _escritura()
    x = _incidente(iid)
    nuevo = request.form.get("estado", "")
    nota = request.form.get("nota", "").strip()[:1000]
    if nuevo not in ORDEN or nuevo == x["estado"]:
        abort(400)
    if nuevo == "cerrado" and not nota:
        flash("Para cerrar el incidente indica la causa y cómo se resolvió", "error")
        return redirect(url_for("consola.seg_incidente", iid=iid))
    ej("UPDATE seg_incidentes SET estado=?, cerrado=?, cierre=CASE WHEN ?='cerrado' THEN ? ELSE cierre END WHERE id=?",
       nuevo, ahora() if nuevo == "cerrado" else None, nuevo, nota, iid)
    linea(iid, g.usuario["usuario"], "estado", f"{NOMBRE_ESTADO.get(x['estado'])} → {NOMBRE_ESTADO[nuevo]}" + (f" · {nota}" if nota else ""))
    auditar(g.usuario["usuario"], g.cliente["id"], "incidente de seguridad: estado", x["numero"], {"de": x["estado"], "a": nuevo})
    return redirect(url_for("consola.seg_incidente", iid=iid))


@bp.route("/seguridad/incidentes-seguridad/<int:iid>/tomar", methods=["POST"])
@requiere_login
def seg_incidente_tomar(iid):
    _escritura()
    x = _incidente(iid)
    ej("UPDATE seg_incidentes SET responsable_usuario=? WHERE id=?", g.usuario["usuario"], iid)
    linea(iid, g.usuario["usuario"], "responsable", f"Lo toma {g.usuario['nombre']} (grupo {x['responsable_grupo']})")
    if x["estado"] == "nuevo":
        ej("UPDATE seg_incidentes SET estado='triage' WHERE id=?", iid)
        linea(iid, g.usuario["usuario"], "estado", "Nuevo → Triage")
    return redirect(url_for("consola.seg_incidente", iid=iid))


@bp.route("/seguridad/incidentes-seguridad/<int:iid>/nota", methods=["POST"])
@requiere_login
def seg_incidente_nota(iid):
    _escritura()
    _incidente(iid)
    t = request.form.get("nota", "").strip()[:2000]
    if t:
        linea(iid, g.usuario["usuario"], "nota", t)
    return redirect(url_for("consola.seg_incidente", iid=iid))


def contener(usuario, cliente, x, accion, prueba=False, eqs=None):
    """Pide la acción de contención como ticket con aprobación en AAP sobre la máquina del incidente."""
    from .operacion import solicitar_en_equipo
    e, sug = sugerencias(x, eqs)
    s = next((y for y in sug if y["accion"] == accion), None)
    if not s:
        raise ValueError("Esa acción no aplica a este incidente")
    if s["no"]:
        raise ValueError(s["no"])
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO_CONTENCION)
    if not a:
        raise ValueError(f"Falta «{AUTO_CONTENCION}» en el catálogo (sincroniza con AAP)")
    permit = [cred(x["siem"]).get("ip") or ""] + [z.get("ip") or "" for z in (siems(eqs) if eqs is not None else [])]
    params = {"sc_accion": accion, "sc_incidente": x["numero"], "sc_permitidas": ",".join(dict.fromkeys(p for p in permit if p))}
    if accion == "bloquear_ip":
        params["sc_ip"] = x["ip_origen"]
    if accion in ("deshabilitar_usuario", "habilitar_usuario"):
        params["sc_usuario"] = x["usuario"]
    t = solicitar_en_equipo(usuario, cliente, a, e, params, f"Incidente de seguridad {x['numero']}: {CONTENCION[accion]}", prueba=prueba)
    linea(x["id"], usuario["usuario"], "contencion", f"{CONTENCION[accion]} en {e['nombre']}: ticket {t['numero']} (con aprobación)", ticket_id=t["id"])
    if accion in ("bloquear_ip", "aislar", "deshabilitar_usuario") and x["estado"] in ("nuevo", "triage"):
        ej("UPDATE seg_incidentes SET estado='contencion' WHERE id=?", x["id"])
        linea(x["id"], usuario["usuario"], "estado", f"{NOMBRE_ESTADO[x['estado']]} → Contención")
    if accion in ("revertir", "habilitar_usuario") and x["estado"] in ("contencion", "erradicacion"):
        ej("UPDATE seg_incidentes SET estado='recuperacion' WHERE id=?", x["id"])
        linea(x["id"], usuario["usuario"], "estado", f"{NOMBRE_ESTADO[x['estado']]} → Recuperación")
    return t


@bp.route("/seguridad/incidentes-seguridad/<int:iid>/contener", methods=["POST"])
@requiere_login
def seg_incidente_contener(iid):
    _escritura()
    x = _incidente(iid)
    try:
        t = contener(g.usuario, g.cliente, x, request.form.get("accion", ""), eqs=equipos_visibles())
        flash(f"{t['numero']} creado: espera la aprobación en AAP", "ok")
    except (ValueError, PermissionError) as err:
        flash(str(err), "error")
    return redirect(url_for("consola.seg_incidente", iid=iid))


# ── Acciones del SIEM (todas con ticket) ─────────────────────────────────────────────────────────────────────────────
def ruta_consola(eid):
    pre = request.script_root or os.environ.get("FACEOPS_PREFIJO", "/faceops")   # la ruta pública con la que el navegador la pide
    return pre + url_for("consola.consola_web_plataforma", eid=eid, cod=SKILL, sub="")[len(request.script_root):].rstrip("/")


def instalar(usuario, cliente, e, ruta, prueba=False):
    from .operacion import solicitar_en_equipo
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO_INSTALAR)
    if not a:
        raise ValueError(f"Falta «{AUTO_INSTALAR}» en el catálogo (sincroniza con AAP)")
    cf = conf(cliente)
    return solicitar_en_equipo(usuario, cliente, a, e, {"wz_ruta": ruta, "wz_nivel": str(cf.get("nivel") or 10)},
                               f"Instalar el SIEM (Wazuh) en {e['nombre']}", prueba=prueba, reinstalar=True)


def enviar_logs(usuario, cliente, e, siem, modo="activar", agente=False, prueba=False):
    from .operacion import solicitar_en_equipo
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO_LOGS)
    if not a:
        raise ValueError(f"Falta «{AUTO_LOGS}» en el catálogo (sincroniza con AAP)")
    c = cred(siem["nombre"])
    if modo == "activar" and not c.get("ca"):
        raise ValueError(f"La CA del receptor de {siem['nombre']} no está en la bóveda: instala o reinstala el SIEM")
    params = {"sl_siem": c.get("ip") or siem["ip"], "sl_siem_nombre": siem["nombre"], "sl_modo": modo, "sl_agente": bool(agente)}
    return solicitar_en_equipo(usuario, cliente, a, e, params,
                               f"{'Enviar' if modo == 'activar' else 'Dejar de enviar'} los logs de {e['nombre']} al SIEM {siem['nombre']}"
                               + (" (con agente de Wazuh)" if agente else " (agentless)"),
                               secretos={"sl_ca": c["ca"]} if modo == "activar" else None, prueba=prueba)


def logs_aap(usuario, cliente, siem, modo="activar", prueba=False):
    from .operacion import solicitar
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO_AAP)
    if not a:
        raise ValueError(f"Falta «{AUTO_AAP}» en el catálogo (sincroniza con AAP)")
    c = cred(siem["nombre"])
    return solicitar(usuario, cliente, a, None, {"sl_siem": c.get("ip") or siem["ip"], "sl_modo": modo},
                     f"{'Enviar' if modo == 'activar' else 'Dejar de enviar'} los logs de AAP al SIEM {siem['nombre']}", prueba=prueba)


def _siem_form():
    s = next((x for x in siems() if x["id"] == request.form.get("siem", type=int)), None)
    if not s:
        abort(404)
    return s


@bp.route("/seguridad/siem/instalar", methods=["POST"])
@requiere_login
def seg_siem_instalar():
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == request.form.get("equipo", type=int)), None)
    if not e:
        abort(404)
    try:
        t = instalar(g.usuario, g.cliente, e, ruta_consola(e["id"]))
        flash(f"{t['numero']} creado: instalación de Wazuh en {e['nombre']} (espera la aprobación en AAP)", "ok")
    except (ValueError, PermissionError) as err:
        flash(str(err), "error")
    return redirect(url_for("consola.seg_incidentes"))


@bp.route("/seguridad/siem/logs", methods=["POST"])
@requiere_login
def seg_siem_logs():
    _escritura()
    s = _siem_form()
    ids = set(request.form.getlist("equipos", type=int))
    modo = "quitar" if request.form.get("modo") == "quitar" else "activar"
    ok, mal = [], []
    for e in [x for x in equipos_visibles() if x["id"] in ids]:
        try:
            ok.append(enviar_logs(g.usuario, g.cliente, e, s, modo, request.form.get("agente") == "1")["numero"])
        except (ValueError, PermissionError) as err:
            mal.append(f"{e['nombre']}: {err}")
    if ok:
        flash(f"Tickets creados: {', '.join(ok)} (esperan la aprobación en AAP)", "ok")
    for m in mal:
        flash(m, "error")
    return redirect(url_for("consola.seg_incidentes"))


@bp.route("/seguridad/siem/aap", methods=["POST"])
@requiere_login
def seg_siem_aap():
    _escritura()
    s = _siem_form()
    try:
        t = logs_aap(g.usuario, g.cliente, s, "quitar" if request.form.get("modo") == "quitar" else "activar")
        flash(f"{t['numero']} creado: logs de AAP al SIEM (espera la aprobación en AAP)", "ok")
    except (ValueError, PermissionError) as err:
        flash(str(err), "error")
    return redirect(url_for("consola.seg_incidentes"))


@bp.route("/seguridad/siem/faceops", methods=["POST"])
@requiere_login
def seg_siem_faceops():
    """Auditoría de FaceOps hacia el SIEM: la envía FaceOps mismo (no toca ninguna máquina)."""
    _escritura()
    s = _siem_form()
    activar = request.form.get("modo") != "quitar"
    if activar and not cred(s["nombre"]).get("ca"):
        flash("La CA del receptor del SIEM no está en la bóveda", "error")
        return redirect(url_for("consola.seg_incidentes"))
    ultimo = (q1("SELECT MAX(id) m FROM auditoria") or {}).get("m") or 0
    ajuste("siem_auditoria", json.dumps({"cliente_id": g.cliente["id"], "siem": s["nombre"], "desde": ultimo, "por": g.usuario["usuario"], "cuando": ahora()})
           if activar else "")
    if activar:
        ajuste("siem_auditoria_ultimo", str(max(0, ultimo - 50)))   # incluye lo último para que se vea de inmediato
    auditar(g.usuario["usuario"], g.cliente["id"], "auditoría de FaceOps al SIEM", s["nombre"], {"activa": activar})
    flash("La auditoría de FaceOps se envía al SIEM" if activar else "La auditoría de FaceOps dejó de enviarse al SIEM", "ok")
    return redirect(url_for("consola.seg_incidentes"))


# ── Auditoría de FaceOps → SIEM (syslog por TLS, un solo emisor entre los procesos) ─────────────────────────────────
def _mensaje(r):
    det = j(r.get("detalle"), {}) if isinstance(r.get("detalle"), str) else (r.get("detalle") or {})
    det = {k: v for k, v in (det or {}).items() if not any(s in k.lower() for s in ("clave", "token", "secret", "password"))}
    cuerpo = {"origen": "faceops", "id": r["id"], "cuando": r["cuando"], "usuario": r["usuario"] or "", "cliente_id": r["cliente_id"],
              "accion": r["accion"] or "", "objeto": r["objeto"] or "", "detalle": json.dumps(det, ensure_ascii=False)[:1500]}
    d = datetime.now()
    ts = f"{d:%b} {d.day:2d} {d:%H:%M:%S}"
    return f"<134>{ts} faceops faceops-auditoria: {json.dumps(cuerpo, ensure_ascii=False)}\n".encode()


def enviar_auditoria(maximo=500):
    """Envía al SIEM las filas de auditoría nuevas. Devuelve cuántas envió (o None si no está activa)."""
    cfg = j(ajuste("siem_auditoria") or "", {}) or {}
    if not cfg.get("siem"):
        return None
    c = cred(cfg["siem"])
    if not (c.get("ca") and c.get("ip")):
        return None
    ultimo = int(ajuste("siem_auditoria_ultimo") or cfg.get("desde") or 0)
    filas = q("SELECT * FROM auditoria WHERE id>? AND (cliente_id=? OR cliente_id IS NULL) ORDER BY id LIMIT ?", ultimo, cfg["cliente_id"], maximo)
    if not filas:
        return 0
    ctx = ssl.create_default_context(cadata=c["ca"])
    with socket.create_connection((c["ip"], int(c.get("syslog_tls") or 6514)), timeout=10) as s_:
        with ctx.wrap_socket(s_, server_hostname=c["ip"]) as t_:
            for r in filas:
                t_.sendall(_mensaje(r))
    ajuste("siem_auditoria_ultimo", str(filas[-1]["id"]))
    return len(filas)


def _emisor():
    try:
        fd = open("/tmp/faceops-siem-auditoria.lock", "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return   # otro proceso de FaceOps ya es el emisor
    while True:
        try:
            enviar_auditoria()
        except Exception:  # noqa: BLE001  el SIEM puede no responder: se reintenta con la misma posición
            pass
        time.sleep(15)


if os.environ.get("FACEOPS_VIGIA", "1") == "1" and os.environ.get("FACEOPS_MAQUETA") != "1":
    threading.Thread(target=_emisor, name="siem-auditoria", daemon=True).start()
