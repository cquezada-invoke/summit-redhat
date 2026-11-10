"""Plano de ejecución: FaceOps → AAP → nodos de ejecución → equipos. Un nodo de ejecución por infraestructura y
ambiente, dentro del segmento de red de ese ambiente (en las nubes, detrás de un nodo hop). Así cada automatización corre
desde dentro del segmento que toca, con la credencial de esa infraestructura y sin abrir la red entre segmentos."""
import re
from collections import Counter

from .bd import j

AMB_ORDEN = {"produccion": 0, "qa": 1, "desarrollo": 2}
AMB_NOMBRE = {"produccion": "Producción", "qa": "QA", "desarrollo": "Desarrollo"}


AMB_CORTO = {"produccion": "prod", "desarrollo": "dev", "qa": "qa"}
VACIAS = {"de", "del", "la", "el", "los", "las", "y"}


def slug(t):
    t = t.lower().translate(str.maketrans("áéíóúñ", "aeioun"))
    pal = [p for p in re.split(r"[^a-z0-9]+", t) if p and p not in VACIAS][:2]
    return "-".join(pal)[:20] or "infra"


def _red_de(e, redes):
    for r in redes:
        if r.get("prefijo") and (e.get("ip") or "").startswith(r["prefijo"]):
            return r
        if r.get("categorias") and (e.get("categoria") or "") in r["categorias"]:
            return r
    return None


TRANSVERSALES = [("routers", "routers"), ("switches", "switches"), ("firewalls", "firewalls"), ("balanceadores", "balanceadores"), ("storage", "storage")]


FUENTES = [("monitoreo", "Monitoreo", ("prometheus", "alertmanager", "zabbix", "dynatrace", "grafana", "monitoreo", "nagios", "datadog", "metric")),
           ("devsecops", "DevSecOps / CI", ("gitea", "gitlab", "github", "jenkins", "tekton", "pipeline", "webhook", "despliegue", "cicd", "ci/cd", "sonar")),
           ("itsm", "Gestión de servicios", ("servicenow", "jira", "itsm", "ticket")),
           ("seguridad", "Seguridad", ("siem", "splunk", "qradar", "crowdstrike", "wazuh", "cve", "amenaza", "firewall")),
           ("mensajeria", "Mensajería", ("kafka", "amq", "mqtt"))]


def fuentes_eventos(activaciones):
    """Herramientas que mandan eventos a AAP (Event-Driven Ansible), agrupadas desde sus activaciones de rulebook."""
    out = {}
    for a in activaciones:
        txt = f"{a.get('name', '')} {a.get('description', '')} {a.get('rulebook_name', '')}".lower()
        k, n = next(((k, n) for k, n, pal in FUENTES if any(p in txt for p in pal)), ("otras", "Otras fuentes"))
        x = out.setdefault(k, {"tipo": k, "nombre": n, "activaciones": [], "ok": False})
        x["activaciones"].append(a.get("name", ""))
        x["ok"] = x["ok"] or (a.get("status") or "").lower() == "running"
    return list(out.values())


def _aap_abierto(x, y, w, h, instancias):
    """Caja de AAP abierta: balanceador, los cuatro componentes con 2 nodos cada uno (alta disponibilidad), Redis y PostgreSQL.
    El controller muestra los nodos de control reales (instancias de AAP); el resto, el primer nodo donde hoy corre la plataforma
    (instalación en contenedores) y el segundo como «por habilitar»."""
    ctrl = [i for i in instancias if i.get("node_type") in ("control", "hybrid")]
    base = (ctrl[0]["hostname"] if ctrl else "")
    gw_, gap, pad = 4, 12, 16
    cw = (w - 2 * pad - 3 * gap) / 4
    def nodos(reales, prefijo):
        n = [{"nombre": r["hostname"], "existe": True, "estado": r.get("node_state", "")} for r in reales][:2]
        if not n and base:
            n = [{"nombre": base, "existe": True, "estado": "en el nodo actual"}]
        while len(n) < 2:
            n.append({"nombre": f"{prefijo}-{len(n) + 1}", "existe": False, "estado": "por habilitar (HA)"})
        return n
    comps = [("Platform gateway", "UI · API · inicio de sesión", nodos([], "gateway")),
             ("Automation controller", "plano de control · malla", nodos(ctrl, "controller")),
             ("Automation hub", "colecciones y EE", nodos([], "hub")),
             ("Event-Driven Ansible", "reacciona a eventos", nodos([], "eda"))]
    grupos = []
    for i, (t, d, ns) in enumerate(comps):
        gx = x + pad + i * (cw + gap)
        nw = (cw - 24) / 2
        grupos.append({"x": gx, "y": y + 100, "w": cw, "h": 118, "titulo": t, "desc": d,
                       "nodos": [dict(nd, x=gx + 8 + j * (nw + 8), y=y + 100 + 50, w=nw, h=52) for j, nd in enumerate(ns)]})
    hw = (w - 2 * pad - gap) / 2
    return {"x": x, "y": y, "w": w, "h": h, "cx": x + w / 2, "grupos": grupos,
            "lb": {"x": x + w / 2 - 170, "y": y + 54, "w": 340, "h": 30},
            "redis": {"x": x + pad, "y": y + 232, "w": hw, "h": 52},
            "pg": {"x": x + pad + hw + gap, "y": y + 232, "w": hw, "h": 52}}


def plano(cliente, eqs, infras, instancias, eventos=None):
    from .vistas import clase_eq, grupo_periferico
    redes = (j(cliente["clasificacion"], {}).get("_topologia") or {}).get("redes", [])
    nombres_inst = {i["hostname"]: i for i in instancias}
    cols = []
    for x in infras:
        todos_ = [e for e in eqs if (e.get("ubicacion") or "") == x["ubicacion"]]
        trans = [e for e in todos_ if grupo_periferico(e) in dict(TRANSVERSALES)]
        es = [e for e in todos_ if e not in trans]
        ambs = sorted({e.get("ambiente") or "produccion" for e in es} or {"produccion"}, key=lambda a: (AMB_ORDEN.get(a, 9), a))
        cajas = []
        for a in ambs:
            ea = [e for e in es if (e.get("ambiente") or "produccion") == a]
            rs = Counter((_red_de(e, redes) or {}).get("detalle", "").split(" ·")[0] or "sin red declarada" for e in ea)
            nodo = f"ee-{slug(x['nombre'])}-{AMB_CORTO.get(a, slug(a))}"
            inst = nombres_inst.get(nodo)
            cajas.append({"ambiente": a, "nombre_amb": AMB_NOMBRE.get(a, a.title()), "equipos": len(ea), "redes": [r for r, _ in rs.most_common(3)],
                          "clases": Counter(clase_eq(e) for e in ea), "nodo": nodo, "existe": bool(inst),
                          "estado": inst["node_state"] if inst else "por habilitar"})
        hop = f"hop-{slug(x['nombre'])}" if x["ambito"] == "nube" else None
        tr = None
        if trans:
            cnt = Counter(grupo_periferico(e) for e in trans)
            nodo_t = f"ee-{slug(x['nombre'])}-trans"
            inst = nombres_inst.get(nodo_t)
            tr = {"nodo": nodo_t, "existe": bool(inst), "estado": inst["node_state"] if inst else "por habilitar", "equipos": len(trans),
                  "tipos": [(n, cnt[k]) for k, n in TRANSVERSALES if cnt.get(k)],
                  "redes": [r for r, _ in Counter((_red_de(e, redes) or {}).get("detalle", "").split(" ·")[0] or "gestión" for e in trans).most_common(3)]}
        cols.append({"infra": x, "cajas": cajas, "hop": hop, "hop_existe": bool(hop and hop in nombres_inst), "trans": tr})
    # --- diagramación (coordenadas del SVG)
    W_CAJA, GAP, PAD = 196, 14, 16
    eventos = eventos or []
    dy = 92 if eventos else 0   # fila de herramientas que mandan eventos a AAP, arriba
    A_W, A_H = 760, 300          # caja de AAP abierta: cada componente separado y en alta disponibilidad
    x0, y_inf = 20, 20 + dy + A_H + 74
    for c in cols:
        n = len(c["cajas"])
        c["x"], c["w"] = x0, max(PAD * 2 + n * W_CAJA + (n - 1) * GAP, 300)
        c["titulo"] = c["infra"]["nombre"] if len(c["infra"]["nombre"]) <= (c["w"] - 66) // 8 else c["infra"]["nombre"][:(c["w"] - 66) // 8 - 1] + "…"
        c["y"], c["h"] = y_inf, (330 if c["hop"] else 290) + (130 if c["trans"] else 0)
        yc = c["y"] + (110 if c["hop"] else 62)
        for i, k in enumerate(c["cajas"]):
            off = (c["w"] - (PAD * 2 + n * W_CAJA + (n - 1) * GAP)) / 2
            k["x"], k["y"], k["w"], k["h"] = c["x"] + PAD + off + i * (W_CAJA + GAP), yc, W_CAJA, 200
            k["nx"], k["ny"] = k["x"] + W_CAJA / 2, yc + 52   # centro del nodo de ejecución
        if c["hop"]:
            c["hx"], c["hy"] = c["x"] + c["w"] / 2, c["y"] + 62
        if c["trans"]:
            t = c["trans"]
            t["x"], t["y"], t["w"], t["h"] = c["x"] + PAD, yc + 214, c["w"] - 2 * PAD, 112
            t["nx"], t["ny"] = t["x"] + 60, t["y"] + 50
            t["cw"] = min(104, (t["w"] - 150) / max(len(t["tipos"]), 1) - 8)
        x0 += c["w"] + 28
    for c in cols:   # nodos de ejecución de cada contexto tecnológico (uno por segmento, más el de servicios transversales)
        c["n_ee"] = len(c["cajas"]) + (1 if c["trans"] else 0)
        c["n_ee_ok"] = sum(1 for k in c["cajas"] if k["existe"]) + (1 if c["trans"] and c["trans"]["existe"] else 0)
    ancho = max(x0 - 8, 2 * (A_W / 2 + 60 + 230 + 10))
    centro = ancho / 2
    from .boveda import disponible as boveda_ok
    import os
    vx = centro + A_W / 2 + 60
    if vx + 230 > ancho:
        ancho = vx + 240
    ev_w = 196
    for i, ev in enumerate(eventos):
        ev.update(x=centro - (len(eventos) * (ev_w + 14) - 14) / 2 + i * (ev_w + 14), y=10, w=ev_w, h=58)
        ev["cx"] = ev["x"] + ev_w / 2
    return {"cols": cols, "ancho": ancho, "alto": y_inf + max([c["h"] for c in cols] or [290]) + 20, "eventos": eventos, "dy": dy,
            "vault": {"x": vx, "y": 10 + dy, "w": 220, "h": 46, "ok": boveda_ok(), "texto": "HashiCorp Vault · credenciales por infraestructura" if boveda_ok() else "por conectar"},
            "git": {"x": vx, "y": 66 + dy, "w": 220, "h": 46, "ok": bool(os.environ.get("FACEOPS_GITEA_TOKEN")),
                    "texto": "Gitea · proyectos y respaldos (SCM)" if os.environ.get("FACEOPS_GITEA_TOKEN") else "por conectar"},
            "aap": _aap_abierto(centro - A_W / 2, 20 + dy, A_W, A_H, instancias),
            "faceops": {"x": 20, "y": 20 + dy + A_H / 2 - 34, "w": 210, "h": 68},
            "instancias": instancias,
            "faltan": sum(1 for c in cols for k in c["cajas"] if not k["existe"]) + sum(1 for c in cols if c["hop"] and not c["hop_existe"])
            + sum(1 for c in cols if c["trans"] and not c["trans"]["existe"])}
