#!/usr/bin/env python3
"""Demo 5 en vivo: estado de solo lectura de los 3 casos de autorremediación.

Expone en 127.0.0.1:9471
  /             página con los diagramas (index.html junto a este archivo)
  /estado.json  estado de cada caso (cache de 3 s, sin secretos)

Lee Prometheus, el controller de AAP y EDA (rule audits de la activación 17) con un
token de alcance «read». El token se entrega por systemd (LoadCredential=token), nunca
va en el código ni en la respuesta. El ticket de Odoo se lee del log del job de remediación.
"""
import json, os, re, ssl, threading, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

AQUI = os.path.dirname(os.path.abspath(__file__))
PROM = os.environ.get("DV_PROM", "http://127.0.0.1:9090")
AAP = os.environ.get("DV_AAP", "https://localhost")
ACTIVACION = int(os.environ.get("DV_ACTIVACION", "17"))
PUERTO = int(os.environ.get("DV_PUERTO", "9471"))
CACHE_S = 3
VIGENCIA_S = 60 * 60          # un ciclo de más de 1 h vuelve a «en reposo»
RESET_WF = 1308

CASOS = [
    {"id": "cola", "alerta": "ColaPagosAcumulada", "regla": "Cola de pagos",
     "remediacion": 44, "provocar": 1305},
    {"id": "certificado", "alerta": "CertificadoPorVencer", "regla": "Certificado TLS",
     "remediacion": 45, "provocar": 1306},
    {"id": "windows", "alerta": "SitioWindowsCaido", "regla": "Sitio IIS",
     "remediacion": 49, "provocar": 1307},
]

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE      # AAP local con certificado propio


def _token():
    d = os.environ.get("CREDENTIALS_DIRECTORY")
    ruta = os.path.join(d, "token") if d else os.environ.get("DV_TOKEN_FILE", "/etc/demo-vivo/token")
    with open(ruta) as f:
        return f.read().strip()


TOKEN = _token()


def _get(url, aap=False, timeout=4):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if aap:
        req.add_header("Authorization", "Bearer " + TOKEN)
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx if aap else None) as r:
        return json.load(r)


def aap(ruta):
    return _get(AAP + "/api/" + ruta, aap=True)


def ts(s):
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))   # Prometheus entrega nanosegundos
    return datetime.fromisoformat(s).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z") if t else None


# ---------- fuentes ----------
def estado_alertas():
    """alertname -> inactiva|pendiente|disparada, más si estuvo disparada en los últimos 60 min."""
    out = {}
    reglas = _get(PROM + "/api/v1/rules?type=alert")["data"]["groups"]
    for g in reglas:
        for r in g["rules"]:
            act = [a for a in r.get("alerts", [])]
            desde = min([ts(a.get("activeAt")) or 0 for a in act] or [0]) or None
            out[r["name"]] = {"estado": {"firing": "disparada", "pending": "pendiente"}.get(r["state"], "inactiva"),
                              "desde": iso(desde)}
    nombres = "|".join(c["alerta"] for c in CASOS)
    q = 'max by (alertname) (max_over_time(ALERTS{alertname=~"%s",alertstate="firing"}[60m]))' % nombres
    res = _get(PROM + "/api/v1/query?" + urllib.parse.urlencode({"query": q}))["data"]["result"]
    for m in res:
        n = m["metric"]["alertname"]
        out.setdefault(n, {"estado": "inactiva"})["disparo_60m"] = True
    return out


def ultima_instancia():
    r = aap("eda/v1/activations/%d/instances/?page_size=1" % ACTIVACION)["results"]
    act = aap("eda/v1/activations/%d/" % ACTIVACION)
    return (r[0]["id"] if r else None), act.get("status")


def auditorias(inst):
    if not inst:
        return []
    return aap("eda/v1/audit-rules/?activation_instance_id=%d&page_size=30&order_by=-fired_at" % inst)["results"]


def ultimo_job(plantilla):
    r = aap("controller/v2/unified_jobs/?unified_job_template=%d&order_by=-id&page_size=1" % plantilla)["results"]
    if not r:
        return None
    j = r[0]
    return {"id": j["id"], "tipo": j["type"], "estado": j["status"], "inicio": j.get("started") or j.get("created"),
            "fin": j.get("finished")}


_tickets = {}
_RE_TICKET = re.compile(r"Ticket #(\d+) · ([^·]+?) · (https://\S+)")


def ticket_de(job):
    """Último mensaje «Ticket #N · Estado · URL» del job de remediación."""
    if not job:
        return None
    jid = job["id"]
    if jid in _tickets and job["estado"] not in ("pending", "waiting", "running"):
        return _tickets[jid]
    ev = aap("controller/v2/jobs/%d/job_events/?event=runner_on_ok&task=Odoo&order_by=-counter&page_size=1" % jid)["results"]
    t = None
    if ev:
        msg = ((ev[0].get("event_data") or {}).get("res") or {}).get("msg") or ""
        m = _RE_TICKET.search(msg)
        if m:
            t = {"numero": int(m.group(1)), "estado": m.group(2).strip(), "url": m.group(3)}
    if job["estado"] not in ("pending", "waiting", "running"):
        _tickets[jid] = t
    return t


# ---------- semáforo ----------
CORRIENDO = ("pending", "waiting", "running", "new")
FALLA = ("failed", "error", "canceled")


def color_job(j):
    if not j:
        return "gris"
    if j["estado"] in CORRIENDO:
        return "ambar"
    if j["estado"] == "successful":
        return "verde"
    return "rojo" if j["estado"] in FALLA else "gris"


def armar_caso(c, alertas, audits, reset):
    ahora = time.time()
    al = alertas.get(c["alerta"], {"estado": "inactiva"})
    prov = ultimo_job(c["provocar"])
    rem = ultimo_job(c["remediacion"])
    aud = next((a for a in audits if a["name"].startswith(c["regla"])), None)
    eda = {"regla": aud["name"], "estado": aud["status"], "hora": aud["fired_at"]} if aud else None

    # inicio del ciclo: lo último que lo abrió (provocar, alerta o regla disparada)
    marcas = [ts(prov["inicio"]) if prov else None, ts(al.get("desde")), ts(eda["hora"]) - 60 if eda else None]
    t0 = max([m for m in marcas if m] or [0])
    if prov and ts(prov["inicio"]) and t0 - ts(prov["inicio"]) < 600:
        t0 = ts(prov["inicio"])     # provocar y su alerta son el mismo ciclo
    reposo = (not t0) or (ahora - t0 > VIGENCIA_S and al["estado"] == "inactiva")
    if (reset and reset["estado"] == "successful" and ts(reset["inicio"]) and ts(reset["inicio"]) > t0
            and al["estado"] == "inactiva"):
        reposo = True

    eda_c = eda if eda and ts(eda["hora"]) >= t0 - 5 else None
    rem_c = rem if rem and ts(rem["inicio"]) and ts(rem["inicio"]) >= t0 - 5 else None
    prov_c = prov if prov and ts(prov["inicio"]) and ts(prov["inicio"]) >= t0 - 5 else None
    tk = ticket_de(rem_c) if rem_c else None
    resuelta = al["estado"] == "inactiva" and (al.get("disparo_60m") or bool(rem_c))
    estado_alerta = "resuelta" if (resuelta and rem_c and rem_c["estado"] == "successful") else al["estado"]

    if reposo:
        cajas = dict.fromkeys(["sistema", "monitoreo", "eda", "aap", "odoo", "resuelto"], "gris")
    else:
        mon = {"pendiente": "ambar", "disparada": "rojo", "resuelta": "verde"}.get(estado_alerta, "gris")
        eda_col = "gris" if not eda_c else ("verde" if eda_c["estado"] == "successful" else
                                           "rojo" if eda_c["estado"] in FALLA else "ambar")
        aap_col = color_job(rem_c)
        if tk:
            odoo = "verde" if tk["estado"].lower() in ("solved", "closed", "resuelto", "cerrado", "done") else "ambar"
        else:
            odoo = "rojo" if aap_col == "rojo" else ("ambar" if aap_col == "ambar" else "gris")
        ok = aap_col == "verde" and al["estado"] == "inactiva"
        if prov_c and prov_c["estado"] in CORRIENDO and not eda_c:
            sis = "ambar"
        elif ok:
            sis = "verde"
        elif (prov_c and prov_c["estado"] in FALLA) or aap_col == "rojo" or al["estado"] != "inactiva" or prov_c or eda_c:
            sis = "rojo"
        else:
            sis = "gris"
        res = "verde" if ok else ("rojo" if aap_col == "rojo" else "ambar")
        if prov_c and prov_c["estado"] in FALLA and not eda_c and al["estado"] == "inactiva":
            res = "gris"        # «Provocar» falló: el problema no llegó a producirse
        cajas = {"sistema": sis, "monitoreo": mon, "eda": eda_col, "aap": aap_col, "odoo": odoo, "resuelto": res}

    return {"id": c["id"], "alerta": {"nombre": c["alerta"], "estado": estado_alerta, "desde": al.get("desde")},
            "provocar": prov, "eda": eda, "remediacion": rem, "ticket": tk,
            "ciclo": {"inicio": iso(t0) if t0 else None, "en_reposo": reposo}, "cajas": cajas}


_cache = {"t": 0, "datos": None}
_lock = threading.Lock()


def estado():
    with _lock:
        if _cache["datos"] and time.time() - _cache["t"] < CACHE_S:
            return _cache["datos"]
        errores = []
        try:
            alertas = estado_alertas()
        except Exception as e:
            alertas, _ = {}, errores.append("prometheus: " + type(e).__name__)
        try:
            inst, est_act = ultima_instancia()
            audits = auditorias(inst)
        except Exception as e:
            inst, est_act, audits = None, None, []
            errores.append("eda: " + type(e).__name__)
        try:
            reset = ultimo_job(RESET_WF)
        except Exception as e:
            reset = None
            errores.append("aap: " + type(e).__name__)
        casos = []
        for c in CASOS:
            try:
                casos.append(armar_caso(c, alertas, audits, reset))
            except Exception as e:
                errores.append("%s: %s" % (c["id"], type(e).__name__))
                casos.append({"id": c["id"], "error": True,
                              "cajas": dict.fromkeys(["sistema", "monitoreo", "eda", "aap", "odoo", "resuelto"], "gris")})
        datos = {"generado": iso(time.time()), "activacion": {"id": ACTIVACION, "estado": est_act},
                 "volver_estado_inicial": reset, "casos": casos, "errores": errores}
        _cache.update(t=time.time(), datos=datos)
        return datos


class H(BaseHTTPRequestHandler):
    server_version = "demo-vivo"
    sys_version = ""

    def _env(self, codigo, cuerpo, tipo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(cuerpo)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            with open(os.path.join(AQUI, "index.html"), "rb") as f:
                return self._env(200, f.read(), "text/html; charset=utf-8")
        if p == "/estado.json":
            return self._env(200, json.dumps(estado(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
        self._env(404, b"no encontrado", "text/plain")

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PUERTO), H).serve_forever()
