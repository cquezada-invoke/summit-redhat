"""Seguridad › Mapa de seguridad: el ecosistema completo (nube, perímetro, comunicaciones, plataformas, servidores y
aplicaciones) con un mapa de calor de cuán vulnerable es cada pieza, y la medida contra un estándar internacional:
NIST Cybersecurity Framework 2.0 (seis funciones, niveles 1 a 4: perfil actual contra perfil objetivo), con referencias a
los CIS Controls v8. Todo sale de datos que FaceOps ya tiene (inventario, hallazgos, CVE, escaneo, flujos, alertas,
respaldos, identidades); lo que no tiene evidencia se muestra como «sin evidencia» y cuenta como brecha."""
import ipaddress
import re

from .bd import j, q, q1

ZONAS = [("nube", "Nube"), ("perimetro", "Perímetro"), ("comunicaciones", "Comunicaciones"), ("plataformas", "Plataformas"),
         ("servidores", "Servidores"), ("aplicaciones", "Aplicaciones")]
DIMS = [("parches", "Parches y CVE", .22), ("configuracion", "Configuración segura", .15), ("cifrado", "Cifrado en tránsito", .12),
        ("exposicion", "Exposición", .12), ("soporte", "Soporte del fabricante", .12), ("identidad", "Identidad y accesos", .10),
        ("deteccion", "Detección", .10), ("recuperacion", "Recuperación", .07)]
PLAT = {"jboss", "eap", "tomcat", "postgresql", "mysql", "mariadb", "sqlserver", "oracle", "redis", "mongodb", "kafka", "artemis", "keycloak",
        "openshift", "kubernetes", "vault", "satellite", "nginx", "haproxy", "wireguard"}
ID_KW = re.compile(r"ssh|root|contraseñ|password|sudo|usuario|acceso|login|clave|pam|mfa|2fa", re.I)
SEG_KW = re.compile(r"seguridad|cve|vulner|tls|ssl|cifr|certific|firewall|selinux|puerto|expuest|ssh|root|contraseñ", re.I)


def _skills(e):
    sk = set(e.get("skills") or [])
    for k in ("skills_auto", "skills_manual"):
        v = e.get(k)
        sk |= set(j(v, []) if isinstance(v, str) else (v or []))
    return sk


def zona_de(e, nube=False):
    c = (e.get("categoria") or "servidor").lower()
    if nube:
        return "nube"
    if c in ("firewall", "balanceador"):
        return "perimetro"
    if c in ("router", "switch") or "wireguard" in _skills(e):
        return "comunicaciones"
    if c.startswith("clúster") or c in ("kubernetes", "openshift", "almacenamiento") or _skills(e) & PLAT:
        return "plataformas"
    return "servidores"


def _publica(ip):
    try:
        a = ipaddress.ip_address((ip or "").strip())
        return not (a.is_private or a.is_loopback or a.is_link_local)
    except ValueError:
        return False


def calcular(cliente, eqs, hallazgos, nube_ids=None):
    """Riesgo por equipo (0 = sin riesgo, 100 = máximo) por dimensión, por zona y el grafo del ecosistema."""
    from .alertas_objeto import tipo_de
    from .ciclo_vida import evaluar
    from .cves import por_equipo
    nube_ids = nube_ids or set()
    cves = por_equipo(cliente["id"], eqs)
    h_eq = {}
    for h in hallazgos or []:
        h_eq.setdefault(h.get("equipo_id"), []).append(h)
    esc = {}
    try:
        from .escaneo import vista as esc_vista
        for h in esc_vista(cliente["id"]).get("hallazgos") or []:
            esc.setdefault(h.get("equipo"), []).append(h)
    except Exception:  # noqa: BLE001
        pass
    # flujos y su cifrado
    try:
        from .arquitectura import flujos_de
        from .topologia import _estados, tls_de
        est = _estados(cliente)
        fl = flujos_de(cliente, eqs)
        sin_tls = [f for f in fl if not tls_de(est, f).get("tipo")]
    except Exception:  # noqa: BLE001
        fl, sin_tls = [], []
    n_sin_tls = {}
    for f in sin_tls:
        for k in ("desde", "hacia"):
            n_sin_tls[f.get(k)] = n_sin_tls.get(f.get(k), 0) + 1
    filas = []
    for e in eqs:
        d = e.get("_datos") if isinstance(e.get("_datos"), dict) else (j(e.get("datos"), {}) or {})
        sk = _skills(e)
        hs = h_eq.get(e["id"], [])
        sev = lambda xs, s_: sum(1 for x in xs if str(x.get("severidad", "")).lower() in s_)  # noqa: E731
        hs_id = [x for x in hs if ID_KW.search(f"{x.get('titulo', '')} {x.get('politica', '')}")]
        hs_cfg = [x for x in hs if x not in hs_id] + esc.get(e["nombre"], [])
        cv = cves.get(e["id"]) or {}
        es_srv = (e.get("categoria") or "servidor") in ("servidor", "almacenamiento", "estación de trabajo")
        try:
            fase = (evaluar(dict(e, _datos=d)) or {}).get("fase")
        except Exception:  # noqa: BLE001
            fase = None
        al = {x.get("id") for x in ((d.get("alertas") or {}).get("alertas") or [])}
        dims = {
            "parches": (min(100, cv.get("criticos", 0) * 40 + cv.get("importantes", 0) * 10) if cv.get("revisado") else (50 if es_srv else 20)),
            "configuracion": min(100, sev(hs_cfg, ("alta", "critica", "crítica", "high")) * 35 + sev(hs_cfg, ("media", "medium")) * 12),
            "cifrado": min(100, n_sin_tls.get(e["nombre"], 0) * 34),
            "exposicion": min(100, (60 if _publica(e.get("ip")) else 0) + (40 if e["id"] in nube_ids else 0)
                              + (20 if (e.get("categoria") or "") in ("firewall", "balanceador") and hs else 0)),
            "soporte": {"fuera": 100, "extendido": 60, "desconocido": 30, None: 30}.get(fase, 0),
            "identidad": min(100, len(hs_id) * 40),
            "deteccion": ((0 if sk & {"monitoreo", "node_exporter", "windows_exporter"} or d.get("monitoreo") else 50) if es_srv else 0)
                         + (0 if al else (50 if tipo_de(e) else 0)),
            "recuperacion": (0 if "respaldo-git" in sk else 100) if es_srv else 0,
        }
        total = round(sum(dims[k] * w for k, _, w in DIMS) / sum(w for _, _, w in DIMS))
        filas.append({"id": e["id"], "nombre": e["nombre"], "zona": zona_de(e, e["id"] in nube_ids), "cat": e.get("categoria") or "servidor",
                      "ip": e.get("ip") or "", "dims": dims, "riesgo": total, "cves": cv.get("criticos", 0), "hall": len(hs) + len(esc.get(e["nombre"], [])),
                      "energia": e.get("energia") or ""})
    # aplicaciones: segmentos de negocio con sus máquinas
    segs = []
    try:
        from .inventario import segmentos_de
        todos = q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"])
        por_seg = {}
        idx = {e["id"]: e for e in eqs}
        for e in eqs:
            for s in segmentos_de(e, todos):
                por_seg.setdefault(s, []).append(e["id"])
        f_id = {f["id"]: f for f in filas}
        for s in todos:
            if (s.get("grupo") or "aplicaciones") != "aplicaciones" or not por_seg.get(s["nombre"]):
                continue
            ms = [f_id[i] for i in por_seg[s["nombre"]] if i in f_id]
            if not ms:
                continue
            dims = {k: round(max(m["dims"][k] for m in ms) * .6 + sum(m["dims"][k] for m in ms) / len(ms) * .4) for k, _, _ in DIMS}
            segs.append({"id": f"seg:{s['nombre']}", "nombre": s["nombre"], "zona": "aplicaciones", "dims": dims, "maquinas": [m["id"] for m in ms],
                         "riesgo": round(sum(dims[k] * w for k, _, w in DIMS) / sum(w for _, _, w in DIMS))})
        del idx
    except Exception:  # noqa: BLE001
        pass
    todos_n = filas + segs
    # matriz de calor zona × dimensión
    calor = []
    for z, zn in ZONAS:
        xs = [x for x in todos_n if x["zona"] == z]
        calor.append({"zona": z, "nombre": zn, "n": len(xs), "celdas": [round(sum(x["dims"][k] for x in xs) / len(xs)) if xs else None for k, _, _ in DIMS],
                      "riesgo": round(sum(x["riesgo"] for x in xs) / len(xs)) if xs else None,
                      "peores": sorted(xs, key=lambda x: -x["riesgo"])[:5]})
    flujos = [{"desde": f.get("desde"), "hacia": f.get("hacia"), "puerto": f.get("puerto"), "tls": f not in sin_tls} for f in fl]
    riesgo = round(sum(x["riesgo"] for x in filas) / len(filas)) if filas else None
    return {"equipos": filas, "segmentos": segs, "calor": calor, "dims": DIMS, "flujos": flujos, "riesgo": riesgo,
            "n_sin_tls": len(sin_tls), "n_flujos": len(fl)}


# ── NIST CSF 2.0 ────────────────────────────────────────────────────────────────────────────────────────────────────
NIVELES = {1: "Parcial", 2: "Informado por el riesgo", 3: "Repetible", 4: "Adaptativo"}
FUNCIONES = [("GV", "Gobernar"), ("ID", "Identificar"), ("PR", "Proteger"), ("DE", "Detectar"), ("RS", "Responder"), ("RC", "Recuperar")]


def nivel(p):
    return None if p is None else (1 if p < 25 else 2 if p < 50 else 3 if p < 75 else 4)


def nist(cliente, m, v):
    """Perfil actual por categoría de NIST CSF 2.0 (evidencia de FaceOps), nivel 1–4 y brecha contra el objetivo."""
    cid = cliente["id"]
    eqs = m["equipos"]
    srv = [x for x in eqs if x["cat"] in ("servidor", "almacenamiento", "estación de trabajo")]
    pct_ok = lambda xs, k, lim=0: round(100 * sum(1 for x in xs if x["dims"][k] <= lim) / len(xs)) if xs else None  # noqa: E731
    try:
        from .politicas_seg import vigentes
        n_pol = len(vigentes(cid))
    except Exception:  # noqa: BLE001
        n_pol = 0
    us = q("SELECT u.totp_activo, u.origen FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id WHERE p.cliente_id=? AND u.activo=1", cid)
    mfa = round(100 * sum(1 for u in us if u["totp_activo"]) / len(us)) if us else None
    fuentes = (q1("SELECT COUNT(*) n FROM fuentes_usuarios WHERE cliente_id=? AND activo=1", cid) or {}).get("n", 0) if _existe("fuentes_usuarios") else 0
    siem = _operativas(cliente, "siem")
    vuln = _operativas(cliente, "vulnerabilidades")
    revisados = round(100 * sum(1 for x in srv if x["dims"]["parches"] < 50) / len(srv)) if srv else None
    tls = round(100 * (m["n_flujos"] - m["n_sin_tls"]) / m["n_flujos"]) if m["n_flujos"] else None
    cat = [
        ("GV", "GV.PO", "Políticas de ciberseguridad", "CIS 1-18 (gobierno)", min(100, n_pol * 20) if n_pol else 0, f"{n_pol} políticas de seguridad vigentes", "Seguridad › Políticas"),
        ("GV", "GV.RR", "Roles, responsabilidades y aprobaciones", "CIS 14", v.get("autos_con_aprobacion_pct"), "Cambios con aprobación por áreas", "Aprobaciones en cada automatización"),
        ("GV", "GV.OV", "Supervisión con indicadores", "—", 100 if v.get("radar_eq_pct") is not None else 0, "Radar de salud, KPI y OKR medidos", "Monitoreo › KPI y OKR"),
        ("ID", "ID.AM", "Inventario de activos", "CIS 1, 2", _prom(v.get("inventario_reciente_pct"), v.get("equipos_con_segmento_pct")), "Inventario reciente y asociado a un segmento", "Recolectar inventario"),
        ("ID", "ID.RA", "Evaluación de riesgos y vulnerabilidades", "CIS 7", _prom(revisados, 100 if vuln else 0), "Servidores con CVE revisados; escáner de vulnerabilidades integrado", "Revisar CVE · Escaneo de seguridad"),
        ("ID", "ID.IM", "Mejora continua", "—", 100 if v.get("aprobaciones_pendientes") == 0 else 50, "Deuda operativa bajo control", "Control de deuda"),
        ("PR", "PR.AA", "Identidades, autenticación y accesos", "CIS 5, 6", _prom(mfa, 100 if fuentes else 0, 100 - m["calor"][4]["celdas"][5] if m["calor"][4]["celdas"][5] is not None else None),
         f"Doble factor {mfa if mfa is not None else '—'} %; fuente corporativa {'conectada' if fuentes else 'no conectada'}", "Integraciones › Usuarios de FaceOps"),
        ("PR", "PR.DS", "Seguridad de los datos (cifrado en tránsito)", "CIS 3", tls, f"{tls if tls is not None else '—'} % de flujos con TLS", "Certificados · Emitir para servicio"),
        ("PR", "PR.PS", "Plataformas seguras: parches y soporte", "CIS 4, 7", _prom(pct_ok(srv, "parches", 10), pct_ok(eqs, "soporte", 0), pct_ok(eqs, "configuracion", 12)),
         "Sin CVE críticos, con soporte del fabricante y sin hallazgos de configuración", "Aplicar parches de seguridad · Migraciones"),
        ("PR", "PR.IR", "Resiliencia de la infraestructura", "CIS 11, 12", _prom(pct_ok(srv, "recuperacion"), v.get("crit_SD3")), "Respaldo continuo y alta disponibilidad", "Respaldo continuo · Replicar con HA"),
        ("DE", "DE.CM", "Monitoreo continuo", "CIS 8, 13", _prom(v.get("alertas_cobertura_pct"), v.get("crit_P1")), "Objetos con alertas en Ansible y agente de métricas", "Botón Alertas de cada objeto"),
        ("DE", "DE.AE", "Análisis de eventos (SIEM y EDA)", "CIS 8, 13", _prom(100 if siem else 0, 100), "SIEM integrado; eventos a Event-Driven Ansible", "Integraciones › SIEM"),
        ("RS", "RS.MA", "Gestión de incidentes", "CIS 17", _prom(100 if v.get("kb_problemas") else 0, v.get("tickets_exito_pct")), "Errores conocidos y tickets con trazabilidad", "Seguridad › Incidentes"),
        ("RS", "RS.MI", "Mitigación automática", "CIS 17", v.get("autos_certificadas_pct"), "Remediaciones probadas (regla de prueba)", "Remediaciones con aprobación"),
        ("RC", "RC.RP", "Plan de recuperación", "CIS 11", _prom(pct_ok(srv, "recuperacion"), v.get("segmentos_rto_pct")), "Respaldo continuo y RTO/RPO por segmento", "Recuperar servidor · ficha del segmento"),
    ]
    out = []
    for fk, fn in FUNCIONES:
        cs = [{"id": c[1], "nombre": c[2], "cis": c[3], "pct": c[4], "nivel": nivel(c[4] if c[4] is not None else 0), "evidencia": c[5], "accion": c[6],
               "sin_evidencia": c[4] is None} for c in cat if c[0] == fk]
        vals = [c["pct"] or 0 for c in cs]
        p = round(sum(vals) / len(vals)) if vals else 0
        out.append({"id": fk, "nombre": fn, "pct": p, "nivel": nivel(p), "categorias": cs})
    return out


def _prom(*xs):
    v = [x for x in xs if x is not None]
    return round(sum(v) / len(v)) if v else None


def _existe(tabla):
    return bool(q1("SELECT name FROM sqlite_master WHERE type='table' AND name=?", tabla))


def _operativas(cliente, cat):
    try:
        from .conexiones import catalogo
        return sum(1 for gr in catalogo(cliente) if gr["clave"] == cat for x in gr["items"] if x["estado"] == "operativa")
    except Exception:  # noqa: BLE001
        return 0
