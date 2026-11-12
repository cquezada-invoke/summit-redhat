"""Autocuidado de la plataforma: AAP cuidándose a sí mismo, a su arquitectura (el anfitrión del laboratorio y sus máquinas
virtuales) y a FaceOps.

Flujo: el exportador del anfitrión (aap/files/autocuidado.py) → Prometheus (reglas de datos/autocuidado.yml) → Alertmanager →
Event-Driven Ansible (:5023) → «Autocuidado · Atender alerta» → POST /api/autocuidado/evento → aquí:
  · cada evento queda registrado; cada remediación es un ticket real por AAP (regla de prueba incluida: si la acción no está
    certificada, queda bloqueada y el evento lo dice);
  · riesgo bajo → FaceOps la aprueba con «Aprobado por Claude (regla de Cristian)» cuando el ticket llega a su aprobación;
    riesgo alto (destructiva o de capacidad) → queda pendiente de una persona; las que piden un dato quedan como propuesta.
  · si AAP está caído, el vigía externo del anfitrión hace solo lo crítico y lo deja en registro.jsonl: FaceOps lo convierte
    en tickets de registro; lo que AAP lanzó directo sin FaceOps (FaceOps caído) también se registra como ticket al volver.
FaceOps no entra al anfitrión: lee el registro del vigía (volumen compartido), Prometheus y Alertmanager (solo lectura)."""
import hashlib
import json
import os
import threading
import time

import requests
import yaml

from .bd import ahora, auditar, con, ej, j, q, q1

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLITICA = os.path.join(RAIZ, "datos", "autocuidado.yml")
DIR = os.environ.get("FACEOPS_AUTOCUIDADO_DIR", "/var/lib/faceops/autocuidado")
PROM = os.environ.get("FACEOPS_PROMETHEUS", "http://127.0.0.1:9090").rstrip("/")
ALERTMANAGER = os.environ.get("FACEOPS_ALERTMANAGER", "http://127.0.0.1:9093").rstrip("/")
COMENTARIO = "Aprobado por Claude (regla de Cristian)"
PREFIJO = "Autocuidado · "
TERMINALES = ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error")
HERRAMIENTA_A_AUTO = {"liberar-disco": "Autocuidado · Liberar disco", "recuperar-vms": "Autocuidado · Recuperar máquinas virtuales",
                      "reiniciar-aap": "Autocuidado · Reiniciar componentes de AAP", "reiniciar-faceops": "Autocuidado · Reiniciar FaceOps y despliegue",
                      "respaldar": "Autocuidado · Respaldar bases de AAP y FaceOps", "renovar-cert": "Autocuidado · Renovar certificados",
                      "crecer-fs": "Autocuidado · Ampliar disco del anfitrión (GCP)"}
_cache = {}
_tabla_ok = {"v": False}


def _tabla():
    if _tabla_ok["v"]:
        return
    con().executescript("""CREATE TABLE IF NOT EXISTS autocuidado_eventos (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, cuando TEXT, regla TEXT, accion TEXT, severidad TEXT DEFAULT '',
      objeto TEXT DEFAULT '', resumen TEXT DEFAULT '', estado TEXT DEFAULT 'firing', origen TEXT DEFAULT 'eda', decision TEXT DEFAULT '',
      ticket_id INTEGER, auto_aprobar INTEGER DEFAULT 0, aprobado INTEGER DEFAULT 0, job_aap INTEGER);
      CREATE INDEX IF NOT EXISTS autocuidado_eventos_c ON autocuidado_eventos(cliente_id, id);""")
    con().commit()
    _tabla_ok["v"] = True


def politica():
    m = os.path.getmtime(POLITICA) if os.path.exists(POLITICA) else 0
    if _cache.get("pol", (None,))[0] != m:
        _cache["pol"] = (m, (yaml.safe_load(open(POLITICA, encoding="utf-8")) or {}) if m else {"acciones": {}, "reglas": []})
    return _cache["pol"][1]


def regla(rid):
    return next((r for r in politica().get("reglas", []) if r["id"] == rid), None)


def cliente():
    cod = os.environ.get("FACEOPS_AUTOCUIDADO_CLIENTE", "lab").lower()
    return q1("SELECT * FROM clientes WHERE lower(codigo)=?", cod) or q1("SELECT * FROM clientes ORDER BY id LIMIT 1")


def usuario_sistema():
    return q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")


def token_valido(cabecera):
    try:
        tok = open(os.path.join(DIR, "token")).read().strip()
    except OSError:
        return False
    return bool(tok) and cabecera == "Bearer " + hashlib.sha256(tok.encode()).hexdigest()


def auto(cid, nombre_aap):
    return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cid, nombre_aap)


def estado_prueba(cid, a):
    from .pruebas import estado
    try:
        return estado(dict(a, cliente_id=cid))
    except Exception as x:  # noqa: BLE001
        return {"estado": "desconocido", "motivo": str(x)[:120]}


def _activo_de(cid, aid):
    return q1(f"SELECT * FROM tickets WHERE cliente_id=? AND automatizacion_id=? AND estado NOT IN ({','.join('?' * len(TERMINALES))}) "
              "AND motivo LIKE 'Autocuidado%' ORDER BY id DESC LIMIT 1", cid, aid, *TERMINALES)


def _evento(cid, ev, origen="eda", decision="", ticket_id=None, auto_aprobar=0, job=None):
    _tabla()
    return ej("INSERT INTO autocuidado_eventos(cliente_id, cuando, regla, accion, severidad, objeto, resumen, estado, origen, decision, "
              "ticket_id, auto_aprobar, job_aap) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", cid, ahora(), ev.get("regla", ""), ev.get("accion", ""),
              ev.get("severidad", ""), ev.get("objeto", ""), (ev.get("resumen") or "")[:400], ev.get("estado", "firing"), origen, decision[:400],
              ticket_id, auto_aprobar, job)


# ── eventos de EDA ─────────────────────────────────────────────────────────────────────────────────────────────────────
def recibir(ev):
    """Aplica la política a un evento de autocuidado. Devuelve {decision, ticket, auto_aprobar}."""
    cl = cliente()
    if not cl:
        return {"decision": "sin cliente en FaceOps"}
    rg = regla(ev.get("regla") or "") or {}
    acc_k = ev.get("accion") or rg.get("accion") or "avisar"
    acc = politica().get("acciones", {}).get(acc_k)
    if not acc:
        _evento(cl["id"], ev, decision=f"acción «{acc_k}» desconocida en la política")
        return {"decision": "acción desconocida"}
    if ev.get("estado") == "resolved":
        _evento(cl["id"], ev, decision="resuelta: el monitoreo volvió a la normalidad")
        return {"decision": "resuelta"}
    if not acc.get("auto"):
        _evento(cl["id"], ev, decision="solo aviso (campana y Monitoreo › Autocuidado)")
        return {"decision": "aviso"}
    a = auto(cl["id"], acc["auto"])
    if not a:
        _evento(cl["id"], ev, decision=f"«{acc['auto']}» aún no está en el catálogo de FaceOps (sincroniza con AAP)")
        return {"decision": "sin acción en el catálogo"}
    if acc.get("pide_datos"):
        _evento(cl["id"], ev, decision=f"propuesta: «{a['nombre']}» necesita {acc['pide_datos']} y aprobación humana ({acc.get('por_que', 'riesgo alto')})")
        return {"decision": "propuesta"}
    act = _activo_de(cl["id"], a["id"])
    if act:
        _evento(cl["id"], ev, decision=f"agrupada con {act['numero']} (ya en curso)", ticket_id=act["id"])
        return {"decision": "agrupada", "ticket": act["numero"]}
    ep = estado_prueba(cl["id"], a)
    if ep.get("estado") != "certificada":
        _evento(cl["id"], ev, decision=f"bloqueada por la regla de prueba ({ep.get('estado')}): no se lanza hasta pasar su plan")
        return {"decision": "bloqueada"}
    u = usuario_sistema()
    from .operacion import solicitar
    motivo = f"Autocuidado · {ev.get('regla')} {rg.get('nombre', '')} · {ev.get('resumen') or ''}".strip()[:300]
    try:
        t = solicitar(u, cl, a, None, dict(acc.get("params") or {}, autocuidado_regla=ev.get("regla", "")), motivo)
    except Exception as x:  # noqa: BLE001
        _evento(cl["id"], ev, decision=f"no se pudo pedir: {str(x)[:200]}")
        return {"decision": "error", "error": str(x)[:200]}
    bajo = acc.get("riesgo") in ("bajo", "lectura")
    _evento(cl["id"], ev, decision=("riesgo bajo: se aprueba con «" + COMENTARIO + "»") if bajo else
            f"riesgo {acc.get('riesgo')}: queda pendiente de aprobación humana ({acc.get('por_que', '')})", ticket_id=t["id"], auto_aprobar=int(bajo))
    auditar("autocuidado", cl["id"], "evento de autocuidado", t["numero"], {"regla": ev.get("regla"), "accion": acc_k, "riesgo": acc.get("riesgo")})
    return {"decision": "ticket", "ticket": t["numero"], "auto_aprobar": bajo}


def pedir_manual(usuario, cl, acc_k, modo):
    """Desde Monitoreo › Autocuidado: una persona pide la acción (siempre con su aprobación normal; nunca se auto-aprueba)."""
    acc = politica().get("acciones", {}).get(acc_k)
    if not acc or not acc.get("auto") or acc.get("pide_datos"):
        raise ValueError("Acción no disponible desde aquí")
    a = auto(cl["id"], acc["auto"])
    if not a:
        raise ValueError(f"«{acc['auto']}» aún no está en el catálogo (sincroniza con AAP)")
    params = dict(acc.get("params") or {})
    if modo == "evaluar" and "ac_modo" in params:
        params["ac_modo"] = "evaluar"
    from .operacion import solicitar
    t = solicitar(usuario, cl, a, None, params, f"Autocuidado · pedido por {usuario['usuario']} ({'evaluar' if modo == 'evaluar' else 'aplicar'})")
    _evento(cl["id"], {"regla": "manual", "accion": acc_k, "resumen": f"pedido por {usuario['usuario']}"}, origen="manual",
            decision="pedido manual: aprobación normal del ticket", ticket_id=t["id"])
    return t


# ── ciclo de fondo ─────────────────────────────────────────────────────────────────────────────────────────────────────
def aprobar_bajo_riesgo():
    """Los tickets de autocuidado de riesgo bajo se aprueban con el comentario de la regla cuando llegan a su aprobación."""
    _tabla()
    from .operacion import resolver_aprobacion
    u = usuario_sistema()
    for e in q("SELECT e.id, e.ticket_id, t.estado FROM autocuidado_eventos e JOIN tickets t ON t.id=e.ticket_id "
               "WHERE e.auto_aprobar=1 AND e.aprobado=0 ORDER BY e.id LIMIT 20"):
        if e["estado"] == "pendiente_aprobacion":
            try:
                resolver_aprobacion(u, e["ticket_id"], True, COMENTARIO)
                ej("UPDATE autocuidado_eventos SET aprobado=1 WHERE id=?", e["id"])
            except Exception as x:  # noqa: BLE001
                print("autocuidado · aprobar:", str(x)[:200], flush=True)
        elif e["estado"] in TERMINALES:
            ej("UPDATE autocuidado_eventos SET aprobado=-1 WHERE id=?", e["id"])   # terminó antes (p. ej. rechazado a mano): no se insiste


def _ticket_registro(cl, a, estado, motivo, resultado, parametros, job=None, cuando=None):
    """Ticket de lo que se hizo FUERA del flujo normal (vigía externo o AAP sin FaceOps): queda en el historial como cualquier otro."""
    from .operacion import numero_ticket
    u = usuario_sistema()
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, motivo, creado, "
             "inicio, fin, valor_min, tarifa, aap_job_tipo, aap_job_id, resultado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cl["id"], numero_ticket(cl), a["id"], (u or {}).get("id"), estado, "", "", json.dumps(parametros, ensure_ascii=False), motivo,
             cuando or ahora(), cuando or ahora(), ahora() if estado in TERMINALES else None, a.get("minutos_manual") or 0, 0, "job", job,
             (resultado or "")[:4000])
    return q1("SELECT * FROM tickets WHERE id=?", tid)


def importar_vigia():
    """Lo que hizo el vigía externo del anfitrión (registro.jsonl, origen «vigía») → tickets de registro y eventos."""
    from .bd import ajuste
    ruta = os.path.join(DIR, "registro.jsonl")
    try:
        st = os.stat(ruta)
    except OSError:
        return 0
    pos = json.loads(ajuste("autocuidado_registro_pos") or "{}")
    ini = pos.get("pos", 0) if pos.get("ino") == st.st_ino and pos.get("pos", 0) <= st.st_size else 0
    cl = cliente()
    n = 0
    with open(ruta, encoding="utf-8", errors="replace") as f:
        f.seek(ini)
        for linea in f:
            try:
                r = json.loads(linea)
            except ValueError:
                continue
            if r.get("origen") != "vigía":
                continue
            a = auto(cl["id"], HERRAMIENTA_A_AUTO.get(r.get("accion"), "")) if cl else None
            if not a:
                continue
            ok = r.get("resultado") == "ok"
            t = _ticket_registro(cl, a, "exitoso" if ok else "fallido",
                                 "Autocuidado · vigía externo del anfitrión (AAP no respondía): " + r.get("accion", ""),
                                 json.dumps(r.get("detalle") or {}, ensure_ascii=False, default=str), {"origen": "vigía externo", "accion": r.get("accion")},
                                 cuando=r.get("cuando"))
            _evento(cl["id"], {"regla": "vigía", "accion": r.get("accion", ""), "resumen": "AAP no respondía: actuó el vigía externo",
                               "severidad": "critico"}, origen="vigía", decision=f"hecho por el vigía ({r.get('resultado')})", ticket_id=t["id"])
            n += 1
        nueva = f.tell()
    ajuste("autocuidado_registro_pos", json.dumps({"ino": st.st_ino, "pos": nueva}))
    return n


def importar_directas():
    """Trabajos «Autocuidado · …» que AAP lanzó directo porque FaceOps no respondía → tickets (una vez cada uno)."""
    from .aap import AAP, disponible
    cl = cliente()
    if not cl or not cl.get("aap_url") or not disponible(cl["aap_url"]):
        return 0
    aap = AAP.de_cliente(cl)
    if not aap:
        return 0
    from datetime import datetime, timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        js = aap.get("jobs/", name__startswith=PREFIJO, created__gt=desde, order_by="-id", page_size=50).get("results", [])
    except Exception:  # noqa: BLE001
        return 0
    ya = {r["aap_job_id"] for r in q("SELECT aap_job_id FROM tickets WHERE cliente_id=? AND aap_job_id IS NOT NULL AND aap_job_tipo='job'", cl["id"])}
    n = 0
    estados = {"successful": "exitoso", "failed": "fallido", "error": "fallido", "canceled": "cancelado"}
    for x in js:
        if x["id"] in ya or x.get("name") == "Autocuidado · Atender alerta":
            continue
        ev = j(x.get("extra_vars"), {}) or {}
        if not ev.get("autocuidado_sin_faceops"):
            continue
        a = auto(cl["id"], x.get("name"))
        if not a:
            continue
        t = _ticket_registro(cl, a, estados.get(x.get("status"), "en_ejecucion"), f"Autocuidado · {ev.get('autocuidado_regla', '')} · lanzada por AAP sin FaceOps",
                             "", ev, job=x["id"], cuando=x.get("created"))
        _evento(cl["id"], {"regla": ev.get("autocuidado_regla", ""), "accion": x.get("name"), "resumen": "FaceOps no respondía: AAP la lanzó directo"},
                origen="aap-directo", decision="lanzada por AAP sin FaceOps (riesgo bajo)", ticket_id=t["id"], job=x["id"])
        n += 1
    return n


# ── lectura del monitoreo (solo lectura, en segundo plano: las páginas leen la caché) ─────────────────────────────────
def _get(url, **params):
    r = requests.get(url, params=params, timeout=(1, 3))
    r.raise_for_status()
    return r.json()


def leer_monitoreo():
    out = {"t": time.time(), "alertas": [], "reglas": {}, "metricas": {}, "error": ""}
    try:
        al = _get(f"{ALERTMANAGER}/api/v2/alerts", filter='faceops_autocuidado="si"', active="true", silenced="false", inhibited="false")
        out["alertas"] = sorted([{"regla": a["labels"].get("faceops_ac_regla", ""), "accion": a["labels"].get("faceops_ac_accion", ""),
                                  "objeto": a["labels"].get("faceops_ac_objeto", ""), "severidad": a["labels"].get("severity", ""),
                                  "resumen": (a.get("annotations") or {}).get("summary", ""), "desde": a.get("startsAt", "")} for a in al],
                                key=lambda x: (x["severidad"] != "critical", x["regla"]))
    except Exception as x:  # noqa: BLE001
        out["error"] = f"Alertmanager: {type(x).__name__}"
    try:
        for g_ in _get(f"{PROM}/api/v1/rules", type="alert").get("data", {}).get("groups", []):
            if g_.get("name") != "faceops-autocuidado":
                continue
            for r in g_.get("rules", []):
                rid = (r.get("labels") or {}).get("faceops_ac_regla", "")
                out["reglas"][rid] = {"estado": r.get("state", ""), "salud": r.get("health", ""), "activas": len(r.get("alerts") or []),
                                      "error": r.get("lastError", "")}
    except Exception as x:  # noqa: BLE001
        out["error"] = (out["error"] + " · " if out["error"] else "") + f"Prometheus: {type(x).__name__}"
    try:
        for s in _get(f"{PROM}/api/v1/query", query='{__name__=~"faceops_ac_.*"}').get("data", {}).get("result", []):
            met = s["metric"]
            n = met.pop("__name__")[len("faceops_ac_"):]
            for k in ("job", "instance", "nombre"):
                met.pop(k, None)
            out["metricas"].setdefault(n, []).append((met, float(s["value"][1])))
    except Exception:  # noqa: BLE001
        pass
    _cache["mon"] = out
    return out


def monitoreo():
    return _cache.get("mon") or {"t": 0, "alertas": [], "reglas": {}, "metricas": {}, "error": "aún sin lectura"}


def campana():
    """Para la campana global (no consulta nada: lee la caché del ciclo de fondo)."""
    m = monitoreo()
    cr = sum(1 for a in m["alertas"] if a["severidad"] == "critical")
    return {"n": len(m["alertas"]), "criticas": cr, "alertas": m["alertas"][:8], "error": m["error"], "edad": int(time.time() - m["t"]) if m["t"] else None}


def vigia_estado():
    try:
        return json.load(open(os.path.join(DIR, "vigia.json")))
    except (OSError, ValueError):
        return {}


def registro_reciente(n=25):
    ruta = os.path.join(DIR, "registro.jsonl")
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            f.seek(max(0, os.path.getsize(ruta) - 200_000))
            lineas = f.read().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for l in reversed(lineas):
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def resumen_metricas():
    """Indicadores para la cabecera de la página (desde la caché de Prometheus)."""
    m = monitoreo()["metricas"]

    def uno(n, **f):
        for et, v in m.get(n, []):
            if all(et.get(k) == x for k, x in f.items()):
                return v
        return None
    return {"disco_raiz": uno("disco_uso_pct", montaje="/"), "inodos_raiz": uno("inodos_uso_pct", montaje="/"),
            "memoria": uno("memoria_uso_pct"), "swap": uno("swap_uso_pct"), "carga": uno("carga15_por_cpu"),
            "aap_api": uno("aap_api_codigo"), "faceops": uno("faceops_codigo"), "faceops_s": uno("faceops_respuesta_segundos"),
            "vms_pausadas": int(sum(v for _, v in m.get("vm_pausada", []))), "vms_caidas": int(sum(v for _, v in m.get("vm_caida", []))),
            "vms_encendidas": int(sum(v for _, v in m.get("vm_encendida", []))),
            "contenedores_mal": [e.get("contenedor") for e, v in m.get("contenedor_ok", []) if v == 0],
            "contenedores": len(m.get("contenedor_ok", [])), "capacidad": uno("aap_capacidad_libre"), "pendientes": uno("aap_trabajos_pendientes"),
            "atraso": uno("autodeploy_atraso_segundos"), "respaldos": {e.get("base"): v for e, v in m.get("respaldo_edad_segundos", [])},
            "certificados": {e.get("servicio"): v for e, v in m.get("certificado_dias", [])}, "cola_atascados": uno("cola_atascados"),
            "discos": sorted([(e.get("montaje"), v) for e, v in m.get("disco_uso_pct", [])])}


def acciones_tabla(cid):
    out = []
    for k, acc in politica().get("acciones", {}).items():
        if not acc.get("auto"):
            continue
        a = auto(cid, acc["auto"])
        ult = q1("SELECT numero, estado, creado, id FROM tickets WHERE cliente_id=? AND automatizacion_id=? ORDER BY id DESC LIMIT 1", cid, a["id"]) if a else None
        out.append({"clave": k, "texto": acc["texto"], "auto": acc["auto"], "riesgo": acc.get("riesgo"), "por_que": acc.get("por_que", ""),
                    "pide_datos": acc.get("pide_datos", ""), "modo": (acc.get("params") or {}).get("ac_modo", ""),
                    "existe": bool(a), "prueba": estado_prueba(cid, a) if a else {"estado": "sin sincronizar"}, "ultimo": ult,
                    "reglas": [r["id"] for r in politica().get("reglas", []) if r.get("accion") == k]})
    return out


def ejecuciones(cid, n=40):
    _tabla()
    return q("SELECT e.*, t.numero, t.estado AS t_estado, t.respuesta FROM autocuidado_eventos e LEFT JOIN tickets t ON t.id=e.ticket_id "
             "WHERE e.cliente_id=? ORDER BY e.id DESC LIMIT ?", cid, n)


_hilo = {"t": None}


def iniciar(app):
    """Ciclo de fondo (un hilo por proceso): monitoreo cada 20 s, aprobaciones de bajo riesgo y registros del vigía cada 30 s."""
    if _hilo["t"] and _hilo["t"].is_alive():
        return

    def bucle():
        n = 0
        while True:
            try:
                leer_monitoreo()
                if n % 3 == 0:
                    with app.app_context():
                        aprobar_bajo_riesgo()
                        importar_vigia()
                        if n % 6 == 0:
                            importar_directas()
            except Exception as x:  # noqa: BLE001
                print("autocuidado:", type(x).__name__, str(x)[:200], flush=True)
            n += 1
            time.sleep(10)
    _hilo["t"] = threading.Thread(target=bucle, daemon=True, name="faceops-autocuidado")
    _hilo["t"].start()
