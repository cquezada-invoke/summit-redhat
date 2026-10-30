"""Manifiesto de arquitectura de un segmento de negocio: el estado aprobado de una aplicación con todos sus componentes,
recursos, flujos de red y su punto de entrada. FaceOps lo compara con el inventario (plan), propone los tickets para
cerrar las diferencias (habilitar servidores, skills, abrir flujos), dibuja sus flujos en el mapa y prueba el recorrido
de punta a punta. Formato: datos/arquitectura/LEEME.md."""
import hashlib
import json
import os
import re
from collections import defaultdict

import yaml

from .bd import ahora, auditar, ej, j, q, q1

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "arquitectura")
SISTEMAS = {"rhel9": "linux", "ubuntu2404": "linux", "windows2022": "windows"}
# Tipos de componente: servidores (se crean con «Habilitar servidor») y servicios de red del camino (firewall, WAF,
# balanceador…), que son equipos o appliances: se asignan a los existentes o se provisionan aparte.
TIPOS_RED = {"firewall": "Firewall", "waf": "WAF", "balanceador": "Balanceador", "proxy": "Proxy", "router": "Router"}
CATEGORIA_TIPO = {"firewall": ["firewall"], "waf": ["firewall", "balanceador"], "balanceador": ["balanceador", "servidor"],
                  "proxy": ["servidor", "balanceador"], "router": ["router"]}
HABILITAR, ACCESO = "FaceOps · Habilitar servidor", "FaceOps · Acceso de red"
FICHA_OBLIGATORIA = {"area_negocio": "área de negocio", "dueno": "dueño de negocio", "responsable_tecnico": "responsable técnico",
                     "criticidad": "alta, media o baja"}
FICHA = [("area_negocio", "Área de negocio"), ("dueno", "Dueño de negocio"), ("responsable_tecnico", "Responsable técnico"),
         ("soporte", "Soporte"), ("criticidad", "Criticidad"), ("horario_servicio", "Horario de servicio"),
         ("ventana_mantencion", "Ventana de mantención"), ("rto", "RTO"), ("rpo", "RPO"), ("datos", "Información que maneja"),
         ("usuarios", "Usuarios")]
REPO_GIT = "https://github.com/cquezada-invoke/summit-redhat"
TOL_RAM, TOL_DISCO = 0.80, 0.85   # la RAM y el disco que ve el sistema operativo son algo menores que lo asignado


# ------------------------------------------------------------------ lectura y validación
def leer(texto):
    """→ (manifiesto, errores)"""
    try:
        m = yaml.safe_load(texto)
    except yaml.YAMLError as e:
        return None, [f"El YAML no se puede leer: {str(e).splitlines()[0]}"]
    if not isinstance(m, dict):
        return None, ["El manifiesto debe ser un mapa YAML (segmento, componentes, flujos…)"]
    return normalizar(m), validar(m)


def normalizar(m):
    m = dict(m)
    m["ambientes"] = [str(a) for a in (m.get("ambientes") or ["produccion"])]
    m["ficha"] = {k: str(v) for k, v in (m.get("ficha") or {}).items()}
    m["externos"] = list(m.get("externos") or [])
    m["componentes"] = list(m.get("componentes") or [])
    m["flujos"] = list(m.get("flujos") or [])
    for c in m["componentes"]:
        r = c.get("replicas", 1)
        c["replicas"] = {a: int(r) for a in m["ambientes"]} if isinstance(r, (int, str)) else {str(k): int(v) for k, v in r.items()}
        c["recursos"] = dict(c.get("recursos") or {})
        c["equipos"] = list(c.get("equipos") or [])
        c.setdefault("nombre", c.get("id", ""))
        c["uso"] = c.get("uso") or "dedicado"   # dedicado | compartido (asignado por Infraestructura a máquinas de otros segmentos)
        c["tipo"] = c.get("tipo") or "servidor"  # servidor | firewall | waf | balanceador | proxy | router
    for f in m["flujos"]:
        p = f.get("puerto")
        f["puertos"] = [str(x) for x in (p if isinstance(p, list) else re.findall(r"\d{1,5}", str(p)))]
        v = f.get("via") or []
        f["via"] = [v] if isinstance(v, str) else list(v)   # servicios de red por los que pasa (firewall, WAF, balanceador)
    return m


def validar(m):
    err = []
    if not str(m.get("segmento") or "").strip():
        err.append("Falta «segmento» (nombre del segmento de negocio)")
    if not re.fullmatch(r"[a-z][a-z0-9]{1,7}", str(m.get("codigo") or "")):
        err.append("«codigo» debe tener 2 a 8 letras minúsculas o números (nombra los servidores nuevos)")
    comps = m.get("componentes") or []
    if not comps:
        err.append("Declara al menos un componente")
    ids = set()
    for c in comps:
        cid = str(c.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9]{1,9}", cid):
            err.append(f"Componente «{cid or '?'}»: el id debe tener 2 a 10 letras minúsculas o números")
        if cid in ids:
            err.append(f"Componente «{cid}» repetido")
        ids.add(cid)
        tipo = c.get("tipo") or "servidor"
        if tipo != "servidor" and tipo not in TIPOS_RED:
            err.append(f"Componente «{cid}»: tipo debe ser servidor o uno de {', '.join(TIPOS_RED)}")
        if (tipo == "servidor" or c.get("sistema")) and c.get("sistema") not in SISTEMAS:
            err.append(f"Componente «{cid}»: sistema debe ser uno de {', '.join(SISTEMAS)}")
        if tipo == "servidor" and not c.get("producto"):
            err.append(f"Componente «{cid}»: falta «producto» (la skill, p. ej. postgres)")
        rec = c.get("recursos") or {}
        for k, (lo, hi) in {"cpu": (1, 8), "ram_gb": (2, 32), "disco_gb": (20, 200)}.items():
            if k in rec and not (lo <= int(rec[k]) <= hi):
                err.append(f"Componente «{cid}»: {k} fuera de rango ({lo}-{hi})")
    ext = {str(x.get("id")) for x in (m.get("externos") or [])}
    for i, f in enumerate(m.get("flujos") or [], 1):
        for k in ("desde", "hacia"):
            if f.get(k) not in ids | ext:
                err.append(f"Flujo {i}: «{k}: {f.get(k)}» no es un componente ni un externo declarado")
        if not re.findall(r"\d{1,5}", str(f.get("puerto"))):
            err.append(f"Flujo {i}: falta el puerto")
        via = f.get("via") or []
        for v in ([via] if isinstance(via, str) else via):
            c = next((x for x in comps if x.get("id") == v), None)
            if not c or (c.get("tipo") or "servidor") == "servidor":
                err.append(f"Flujo {i}: «via: {v}» debe ser un componente de red (firewall, waf, balanceador, proxy o router)")
    fi = m.get("ficha") or {}
    for k, t in FICHA_OBLIGATORIA.items():
        if not str(fi.get(k) or "").strip():
            err.append(f"Falta ficha.{k} ({t})")
    if fi.get("criticidad") and fi["criticidad"] not in ("alta", "media", "baja"):
        err.append("ficha.criticidad debe ser alta, media o baja")
    en = m.get("entrada") or {}
    if en and en.get("hacia") not in ids:
        err.append("«entrada.hacia» debe ser un componente")
    return err


def hash_de(texto):
    return hashlib.sha256(texto.strip().encode()).hexdigest()[:16]


# ------------------------------------------------------------------ versiones
def vigente(cliente_id, segmento):
    r = q1("SELECT * FROM arquitecturas WHERE cliente_id=? AND segmento=? AND estado='aprobada' ORDER BY version DESC LIMIT 1",
           cliente_id, segmento)
    if r:
        r["m"] = normalizar(yaml.safe_load(r["contenido"]))
    return r


def vigentes(cliente_id):
    out = {}
    for r in q("SELECT * FROM arquitecturas WHERE cliente_id=? AND estado='aprobada' ORDER BY version", cliente_id):
        out[r["segmento"]] = r
    for r in out.values():
        r["m"] = normalizar(yaml.safe_load(r["contenido"]))
    return out


def proponer(cliente, texto, autor, origen="faceops"):
    m, err = leer(texto)
    if err:
        raise ValueError("; ".join(err))
    ultima = q1("SELECT MAX(version) v FROM arquitecturas WHERE cliente_id=? AND segmento=?", cliente["id"], m["segmento"])
    v = (ultima["v"] or 0) + 1
    aid = ej("INSERT INTO arquitecturas(cliente_id, segmento, version, contenido, hash, estado, origen, autor, creado) "
             "VALUES (?,?,?,?,?,?,?,?,?)", cliente["id"], m["segmento"], v, texto, hash_de(texto), "propuesta", origen, autor, ahora())
    auditar(autor, cliente["id"], "proponer arquitectura", m["segmento"], {"version": v, "origen": origen})
    return aid


def aprobar(cliente, aid, quien):
    a = q1("SELECT * FROM arquitecturas WHERE id=? AND cliente_id=?", aid, cliente["id"])
    if not a or a["estado"] != "propuesta":
        raise ValueError("Esa versión no está pendiente de aprobación")
    ej("UPDATE arquitecturas SET estado='reemplazada' WHERE cliente_id=? AND segmento=? AND estado='aprobada'", cliente["id"], a["segmento"])
    ej("UPDATE arquitecturas SET estado='aprobada', aprobado_por=?, aprobado_en=? WHERE id=?", quien, ahora(), aid)
    aplicar_segmento(cliente, normalizar(yaml.safe_load(a["contenido"])))
    auditar(quien, cliente["id"], "aprobar arquitectura", a["segmento"], {"version": a["version"]})


def rechazar(cliente, aid, quien):
    ej("UPDATE arquitecturas SET estado='rechazada', aprobado_por=?, aprobado_en=? WHERE id=? AND cliente_id=? AND estado='propuesta'",
       quien, ahora(), aid, cliente["id"])


def cargar_desde_git(cliente):
    """Los manifiestos del repositorio (aprobados por Pull Request) entran como versión aprobada si cambiaron."""
    if not os.path.isdir(DIR):
        return 0
    commits = {}
    try:
        for linea in open(os.path.join(DIR, "commits.txt"), encoding="utf-8"):
            p = linea.rstrip("\n").split("|", 4)
            if len(p) == 5 and p[1]:
                commits[p[0]] = {"sha": p[1], "autor": p[2], "fecha": p[3], "mensaje": p[4][:120],
                                 "url": f"{REPO_GIT}/blob/{p[1]}/faceops/datos/arquitectura/{p[0]}"}
    except OSError:
        pass
    n = 0
    for f in sorted(os.listdir(DIR)):
        if not f.endswith((".yml", ".yaml")):
            continue
        texto = open(os.path.join(DIR, f), encoding="utf-8").read()
        m, err = leer(texto)
        if err:
            auditar("sistema", cliente["id"], "arquitectura con errores", f, {"errores": err})
            continue
        act = vigente(cliente["id"], m["segmento"])
        git = json.dumps(dict(commits.get(f, {}), archivo=f), ensure_ascii=False)
        if act and act["hash"] == hash_de(texto):
            ej("UPDATE arquitecturas SET git=? WHERE id=?", git, act["id"])
            aplicar_segmento(cliente, m)
            continue
        aid = proponer(cliente, texto, "Git (Pull Request aprobado)", origen="git")
        ej("UPDATE arquitecturas SET git=? WHERE id=?", git, aid)
        aprobar(cliente, aid, "Git · " + f)
        n += 1
    return n


def aplicar_segmento(cliente, m):
    """El segmento de negocio queda definido por la arquitectura: sus equipos declarados y los servidores que FaceOps
    cree para ella (srv-<codigo>-…). Puede mezclar sistemas: es la aplicación completa."""
    nombres = sorted({e for c in m["componentes"] for e in c["equipos"]}) + [f"srv-{m['codigo']}-*"]
    regla = json.dumps({"nombre": nombres}, ensure_ascii=False)
    ficha = json.dumps(m.get("ficha") or {}, ensure_ascii=False)
    if q1("SELECT id FROM segmentos WHERE cliente_id=? AND nombre=?", cliente["id"], m["segmento"]):
        ej("UPDATE segmentos SET regla=?, descripcion=?, sistema='', tipo='negocio', ficha=? WHERE cliente_id=? AND nombre=?",
           regla, m.get("descripcion", ""), ficha, cliente["id"], m["segmento"])
    else:
        orden = (q1("SELECT MAX(orden) o FROM segmentos WHERE cliente_id=?", cliente["id"])["o"] or 0) + 1
        ej("INSERT INTO segmentos(cliente_id, nombre, tipo, regla, descripcion, orden, sistema, ficha) VALUES (?,?,?,?,?,?,'',?)",
           cliente["id"], m["segmento"], "negocio", regla, m.get("descripcion", ""), orden, ficha)


# ------------------------------------------------------------------ asignación: qué equipos cumplen cada componente
def _familia(e):
    from .inventario import sistema_eq
    return sistema_eq(e)


def asignacion(m, eqs):
    """{componente: {ambiente: [equipos]}} — los declarados en «equipos» más los creados por FaceOps (srv-<codigo>-<id>-NN)."""
    por_nombre = {e["nombre"]: e for e in eqs}
    out = {}
    for c in m["componentes"]:
        prefijo = f"srv-{m['codigo']}-{c['id']}-"
        lista = [por_nombre[n] for n in c["equipos"] if n in por_nombre] + \
                [e for e in eqs if e["nombre"].startswith(prefijo) and e["nombre"] not in c["equipos"]]
        if c["tipo"] != "servidor" and not c.get("sistema"):
            # un appliance de red (firewall, WAF…) atiende a todos los ambientes que lo declaran, no tiene ambiente propio
            out[c["id"]] = {a: list(lista) for a, n in c["replicas"].items() if n} if lista else {}
            continue
        amb = defaultdict(list)
        for e in lista:
            amb[e.get("ambiente") or m["ambientes"][0]].append(e)
        out[c["id"]] = dict(amb)
    return out


def flujos_expandidos(m, asig):
    """Flujos del manifiesto llevados a equipos: de cada origen hacia los destinos de su mismo ambiente (los ambientes
    no se mezclan)."""
    ext = {x["id"]: x for x in m["externos"]}
    comps = {c["id"]: c for c in m["componentes"]}

    def via_de(f, amb):
        """Servicios de red del camino, con el equipo que cumple ese rol en el ambiente (o sin asignar)."""
        out_v = []
        for v in f.get("via", []):
            c = comps.get(v, {})
            es = asig.get(v, {}).get(amb) or [x for xs in asig.get(v, {}).values() for x in xs]
            out_v.append({"id": v, "tipo": c.get("tipo", ""), "nombre": c.get("nombre", v), "equipo": es[0]["nombre"] if es else None})
        return out_v
    out = []
    for orden, f in enumerate(m["flujos"]):
        base = {"puerto": " · ".join(f["puertos"]), "texto": f.get("motivo", ""), "segmento": m["segmento"],
                "latencia_max_ms": f.get("latencia_max_ms"), "orden": orden, "desde_c": f["desde"], "hacia_c": f["hacia"]}
        destinos = asig.get(f["hacia"], {})
        if f["desde"] in ext:
            for amb, es in destinos.items():
                for b in es:
                    out.append(dict(base, desde="x:" + f["desde"], hacia=b["nombre"], externo=ext[f["desde"]].get("nombre", f["desde"]),
                                    via=via_de(f, amb)))
            continue
        for amb, es in asig.get(f["desde"], {}).items():
            # cada ambiente es completo y aislado: un JBoss de QA solo habla con los componentes de QA (si un
            # componente de QA falta, el flujo no existe y el plan propone crearlo)
            dest = destinos.get(amb) or []
            for a in es:
                for b in dest:
                    if a["nombre"] != b["nombre"]:
                        out.append(dict(base, desde=a["nombre"], hacia=b["nombre"], via=via_de(f, amb)))
    return out


def flujos_de(cliente, eqs=None):
    """Flujos que dibuja y verifica el mapa: los de las arquitecturas vigentes más los de la topología del cliente que
    no estén cubiertos por una arquitectura."""
    topo = j(cliente.get("clasificacion"), {}).get("_topologia", {})
    if eqs is None:
        eqs = q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente["id"])
    arq = []
    for r in vigentes(cliente["id"]).values():
        arq += flujos_expandidos(r["m"], asignacion(r["m"], eqs))
    pares = {(f["desde"], f["hacia"]) for f in arq}
    return [f for f in topo.get("flujos", []) if (f.get("desde"), f.get("hacia")) not in pares] + arq


# ------------------------------------------------------------------ plan: arquitectura vs. inventario
def _producto_ok(e, producto):
    from .inventario import skills_de
    return producto in skills_de(e)


def _recursos(e, rec):
    """Diferencias de recursos del equipo contra lo declarado (lista de textos)."""
    out = []
    if rec.get("cpu") and e.get("cpu") and int(e["cpu"]) < int(rec["cpu"]):
        out.append(f"{e['cpu']} vCPU de {rec['cpu']}")
    if rec.get("ram_gb") and e.get("ram_mb") and e["ram_mb"] < rec["ram_gb"] * 1024 * TOL_RAM:
        out.append(f"{e['ram_mb'] / 1024:.1f} GB de RAM de {rec['ram_gb']}")
    disco = sum(d.get("gb", 0) for d in j(e.get("discos"), []))
    if rec.get("disco_gb") and disco and disco < rec["disco_gb"] * TOL_DISCO:
        out.append(f"{disco:.0f} GB de disco de {rec['disco_gb']}")
    return out


def tickets_en_curso(cliente_id):
    """Tickets activos por automatización: los pasos del plan no se duplican mientras corren."""
    out = defaultdict(list)
    for t in q("SELECT t.id, t.numero, t.estado, t.parametros, t.limite, a.nombre_aap FROM tickets t JOIN automatizaciones a "
               "ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.estado IN ('en_cola','en_ejecucion','pendiente_aprobacion')",
               cliente_id):
        t["p"] = j(t["parametros"], {})
        out[t["nombre_aap"]].append(t)
    return out


def plan(cliente, m, eqs):
    """Compara la arquitectura con el inventario y arma los pasos para cerrar las diferencias."""
    from .topologia import _estado_flujo, _estados, latencia
    asig = asignacion(m, eqs)
    curso = tickets_en_curso(cliente["id"])
    skills_auto = {s["nombre"]: s.get("automatizacion") for s in q("SELECT nombre, automatizacion FROM skills WHERE cliente_id=?", cliente["id"])}
    destino = "gcp" if "gcp" in str(m.get("centro_de_datos", "")).lower() else "onpremise"
    comps, pasos, desv = [], [], []
    for c in m["componentes"]:
        filas = []
        for amb in m["ambientes"]:
            req = c["replicas"].get(amb, 0)
            tiene = asig.get(c["id"], {}).get(amb, [])
            if not req and not tiene:
                continue
            equipos = []
            red = c["tipo"] != "servidor" and not c.get("sistema")   # appliance de red (un balanceador sobre RHEL es servidor)
            for e in tiene:
                d = {"nombre": e["nombre"], "id": e["id"], "energia": e.get("energia"), "ip": e.get("ip"),
                     "producto": True if (red and not c.get("producto")) else _producto_ok(e, c["producto"]),
                     "recursos": [] if red else _recursos(e, c["recursos"]),
                     "sistema_ok": True if red else _familia(e) == SISTEMAS[c["sistema"]]}
                equipos.append(d)
                if not d["sistema_ok"]:
                    desv.append({"sev": "alta", "texto": f"{e['nombre']}: el sistema no es {c['sistema']}", "equipo": e["nombre"]})
                if d["recursos"]:
                    desv.append({"sev": "media", "texto": f"{e['nombre']}: recursos bajo lo declarado ({', '.join(d['recursos'])})",
                                 "equipo": e["nombre"]})
                if not d["producto"]:
                    sa = skills_auto.get(c["producto"])
                    en = next((t for t in curso.get(sa or "", []) if t["limite"] == e["nombre"]), None)
                    pasos.append({"tipo": "skill", "clave": f"skill:{e['nombre']}:{c['producto']}", "componente": c["id"],
                                  "texto": f"Habilitar {c['producto']} en {e['nombre']}", "equipo": e["nombre"], "automatizacion": sa,
                                  "ticket": en, "posible": bool(sa)})
                    desv.append({"sev": "alta", "texto": f"{e['nombre']}: no tiene {c['producto']} andando", "equipo": e["nombre"]})
            faltan = max(0, req - len(tiene))
            if faltan and red:  # un firewall, WAF o balanceador no se crea con «Habilitar servidor»: se asigna o se provisiona
                pasos.append({"tipo": "asignar", "clave": f"asig:{c['id']}:{amb}", "componente": c["id"], "ambiente": amb, "ticket": None,
                              "posible": False, "texto": f"Asignar {TIPOS_RED[c['tipo']].lower()} «{c['nombre']}» ({amb}): un equipo existente o un appliance nuevo"})
                desv.append({"sev": "alta", "texto": f"{c['nombre']} ({amb}): {TIPOS_RED[c['tipo']].lower()} sin asignar"})
            elif faltan:
                base = f"{m['codigo']}-{c['id']}"
                en = next((t for t in curso.get(HABILITAR, []) if t["p"].get("hab_nombre") == base and t["p"].get("hab_ambiente") == amb), None)
                pasos.append({"tipo": "habilitar", "clave": f"hab:{c['id']}:{amb}", "componente": c["id"], "ambiente": amb,
                              "texto": f"Crear {faltan} servidor{'es' if faltan > 1 else ''} {c['sistema']} para {c['nombre']} ({amb})",
                              "ticket": en, "posible": True,
                              "params": {"hab_so": c["sistema"], "hab_destino": destino, "hab_segmento": m["segmento"], "hab_nombre": base,
                                         "hab_replicas": str(faltan), "hab_cpu": str(c["recursos"].get("cpu", 2)),
                                         "hab_ram_gb": str(c["recursos"].get("ram_gb", 4)), "hab_disco_gb": str(c["recursos"].get("disco_gb", 40)),
                                         "hab_ambiente": amb}})
                desv.append({"sev": "alta", "texto": f"{c['nombre']} ({amb}): {len(tiene)} de {req} servidores"})
            if len(tiene) > req:
                desv.append({"sev": "baja", "texto": f"{c['nombre']} ({amb}): {len(tiene)} servidores, la arquitectura declara {req}"})
            filas.append({"ambiente": amb, "requeridas": req, "equipos": equipos, "faltan": faltan})
        comps.append(dict(c, filas=filas))
    # flujos
    estados = _estados(cliente)
    por_nombre = {e["nombre"]: e for e in eqs}
    acceso_curso = {(t["p"].get("origen"), t["p"].get("destino")): t for t in curso.get(ACCESO, [])}
    fl = []
    for f in flujos_expandidos(m, asig):
        if f["desde"].startswith("x:"):
            fl.append(dict(f, estado="entrada", lat=""))
            continue
        est, cuando = _estado_flujo(estados, f)
        lat, _ = latencia(estados, f)
        a, b = por_nombre[f["desde"]], por_nombre[f["hacia"]]
        tk = acceso_curso.get((a.get("ip"), b.get("ip")))
        d = dict(f, estado=est, verificado=cuando, lat=lat, origen_ip=a.get("ip"), destino_ip=b.get("ip"), ticket=tk)
        fl.append(d)
        if est == "bloqueado":
            pasos.append({"tipo": "flujo", "clave": f"flujo:{f['desde']}>{f['hacia']}", "texto": f"Abrir {f['desde']} → {f['hacia']} ({f['puerto']})",
                          "ticket": tk, "posible": True, "origen": a.get("ip"), "destino": b.get("ip"), "puerto": f["puerto"].replace(" · ", ", "),
                          "desde": f["desde"], "hacia": f["hacia"]})
            desv.append({"sev": "alta", "texto": f"Flujo {f['desde']} → {f['hacia']}:{f['puerto']} cerrado ({f['texto']})", "equipo": f["desde"]})
        elif est == "sin verificar":
            pasos.append({"tipo": "verificar", "clave": f"ver:{f['desde']}>{f['hacia']}", "texto": f"Verificar {f['desde']} → {f['hacia']} ({f['puerto']})",
                          "posible": True, "desde": f["desde"], "hacia": f["hacia"], "ticket": None})
        if lat and f.get("latencia_max_ms"):
            v = float(lat.split()[0])
            d["lat_excede"] = v > float(f["latencia_max_ms"])
            if d["lat_excede"]:
                desv.append({"sev": "media", "texto": f"Latencia {f['desde']} → {f['hacia']}: {lat} (máximo {f['latencia_max_ms']} ms)",
                             "equipo": f["desde"]})
    listos = sum(1 for c in comps for r in c["filas"] if not r["faltan"] and all(e["producto"] for e in r["equipos"]))
    total = sum(len(c["filas"]) for c in comps)
    return {"componentes": comps, "flujos": fl, "pasos": pasos, "desviaciones": desv,
            "resumen": {"componentes_ok": listos, "componentes": total,
                        "flujos_ok": sum(1 for f in fl if f["estado"] == "abierto"),
                        "flujos": sum(1 for f in fl if f["estado"] != "entrada"),
                        "pasos": sum(1 for p in pasos if not p.get("ticket") and p.get("posible") and p["tipo"] != "verificar"),
                        "sin_verificar": sum(1 for p in pasos if p["tipo"] == "verificar")}}


# ------------------------------------------------------------------ ejecutar pasos (tickets de una vez)
def ejecutar_paso(usuario, cliente, m, paso, eqs):
    from .operacion import solicitar, solicitar_en_equipo
    motivo = f"Arquitectura «{m['segmento']}» v{m.get('version', 1)}: {paso['texto']}"
    if paso["tipo"] == "habilitar":
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], HABILITAR)
        if not a:
            raise ValueError("Falta «Habilitar servidor» en el catálogo")
        return solicitar(usuario, cliente, a, None, paso["params"], motivo)
    if paso["tipo"] == "skill":
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], paso["automatizacion"] or "")
        e = next((x for x in eqs if x["nombre"] == paso["equipo"]), None)
        if not a or not e:
            raise ValueError("No hay una automatización de skill para ese producto")
        params = {c["var"]: c["defecto"] for c in j(a["campos"], []) if c.get("defecto") not in (None, "") and c["tipo"] not in ("equipo", "equipos")}
        if any(c.get("requerido") and c["var"] not in params and c["tipo"] not in ("equipo", "equipos") for c in j(a["campos"], [])):
            raise ValueError(f"«{a['nombre']}» pide datos: lánzala desde el equipo")
        return solicitar_en_equipo(usuario, cliente, a, e, params, motivo)
    if paso["tipo"] == "flujo":
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], ACCESO)
        if not a:
            raise ValueError("Falta «Acceso de red» en el catálogo")
        return solicitar(usuario, cliente, a, None, {"red_accion": "abrir", "origen": paso["origen"], "destino": paso["destino"],
                                                     "puerto": paso["puerto"]}, motivo)
    raise ValueError("Paso desconocido")


# ------------------------------------------------------------------ prueba del recorrido (de punta a punta)
def probar_entrada(m):
    """Llama la URL de entrada (si está declarada) y revisa el texto esperado. No cambia nada."""
    import requests
    en = m.get("entrada") or {}
    if not en.get("url"):
        return None
    try:
        r = requests.get(en["url"], timeout=6, verify=False)
        texto = re.sub(r"<[^>]+>", " ", r.text)
        ok = r.status_code < 400 and (not en.get("espera") or en["espera"] in r.text)
        linea = ""
        if en.get("espera"):
            clave = en["espera"].split(":")[0]
            mm = re.search(re.escape(clave) + r"[^\n<]{0,60}", r.text)
            linea = mm.group(0).strip() if mm else ""
        return {"ok": ok, "codigo": r.status_code, "ms": round(r.elapsed.total_seconds() * 1000, 1), "linea": linea,
                "servidor": r.headers.get("X-Servidor-JBoss", ""), "espera": en.get("espera", ""),
                "resumen": " ".join(texto.split())[:160]}
    except requests.RequestException as e:
        return {"ok": False, "codigo": 0, "ms": None, "error": str(e).split(":")[0][:120], "espera": en.get("espera", "")}


def probar_recorrido(usuario, cliente, m, eqs):
    """Prueba la entrada (HTTP) y lanza en AAP la verificación de cada tramo del segmento (puerto y latencia)."""
    from .topologia import verificar_flujos
    entrada = probar_entrada(m)
    pares = {(f["desde"], f["hacia"]) for f in flujos_expandidos(m, asignacion(m, eqs)) if not f["desde"].startswith("x:")}
    try:
        n, jobs = verificar_flujos(cliente, eqs, usuario["usuario"], solo=pares) if pares else (0, [])
        error = ""
    except Exception as e:  # noqa: BLE001 — la prueba de entrada vale igual
        n, jobs, error = 0, [], str(e)
    res = {"entrada": entrada, "tramos": n, "trabajos": jobs, "error": error, "por": usuario["usuario"]}
    ej("INSERT INTO recorridos(cliente_id, segmento, cuando, resultado) VALUES (?,?,?,?) "
       "ON CONFLICT(cliente_id, segmento) DO UPDATE SET cuando=excluded.cuando, resultado=excluded.resultado",
       cliente["id"], m["segmento"], ahora(), json.dumps(res, ensure_ascii=False))
    auditar(usuario["usuario"], cliente["id"], "probar recorrido", m["segmento"], {"entrada": (entrada or {}).get("ok"), "tramos": n})
    return res


def ultimo_recorrido(cliente_id, segmento):
    r = q1("SELECT * FROM recorridos WHERE cliente_id=? AND segmento=?", cliente_id, segmento)
    if r:
        r["r"] = j(r["resultado"], {})
    return r


# ------------------------------------------------------------------ hallazgos de arquitectura (por equipo)
def hallazgos(cliente, eqs):
    """Desviaciones de cada arquitectura vigente que se pueden atribuir a un equipo → hallazgos del inventario."""
    from .inventario import nuevo
    por_nombre = {e["nombre"]: e for e in eqs}
    for r in vigentes(cliente["id"]).values():
        for d in plan(cliente, r["m"], eqs)["desviaciones"]:
            e = por_nombre.get(d.get("equipo") or "")
            if e:
                nuevo(cliente["id"], e, "arquitectura", d["sev"], "Arquitectura: " + d["texto"].split(": ", 1)[-1][:90], d["texto"],
                      politica=f"Arquitectura «{r['segmento']}» v{r['version']}")


# ------------------------------------------------------------------ costo de infraestructura del segmento
COSTOS = os.path.join(os.path.dirname(DIR), "costos.yml")


def tarifas():
    try:
        return yaml.safe_load(open(COSTOS, encoding="utf-8")) or {}
    except OSError:
        return {}


def costo(m, eqs=None):
    """Valor mensual de la infraestructura del segmento según lo declarado.
    dedicado: el segmento paga sus servidores completos (recursos + licencia + base + operación).
    compartido (asignado a máquinas existentes de otros segmentos): paga solo los recursos que consume.
    incremental: lo que cuesta crear los servidores que todavía faltan (nueva inversión)."""
    t = tarifas()
    cen = (t.get("centros") or {}).get(m.get("centro_de_datos"), next(iter((t.get("centros") or {"": {}}).values())))
    asig = asignacion(m, eqs) if eqs is not None else {}
    filas, total, incremental = [], 0.0, 0.0
    for c in m["componentes"]:
        r = c["recursos"]
        for amb, n in c["replicas"].items():
            if not n:
                continue
            compartido = (c.get("uso_ambiente") or {}).get(amb, c.get("uso")) == "compartido"
            if c["tipo"] != "servidor" and not c.get("sistema"):  # appliance de red: tarifa propia (dedicado) o una fracción si se comparte
                tr = (t.get("servicios_red") or {}).get(c["tipo"], {})
                recursos, fijo = (tr.get("compartido", 0) if compartido else 0), (0 if compartido else tr.get("dedicado", 0))
            else:
                recursos = r.get("cpu", 0) * cen.get("vcpu", 0) + r.get("ram_gb", 0) * cen.get("ram_gb", 0) + r.get("disco_gb", 0) * cen.get("disco_gb", 0)
                fijo = (t.get("licencias") or {}).get(c.get("sistema"), 0) + cen.get("base_servidor", 0) + t.get("operacion_servidor", 0)
            tiene = len(asig.get(c["id"], {}).get(amb, [])) if eqs is not None else (n if c["equipos"] else 0)
            faltan = max(0, n - tiene)
            mensual = n * (recursos + (0 if compartido else fijo))
            inc = faltan * (recursos + fijo)
            filas.append({"componente": c["nombre"], "id": c["id"], "tipo": c["tipo"], "sistema": c.get("sistema"), "ambiente": amb, "replicas": n, "uso": "compartido" if compartido else "dedicado",
                          "recursos": round(n * recursos, 2), "fijo": 0 if compartido else round(n * fijo, 2), "mensual": round(mensual, 2),
                          "faltan": faltan, "incremental": round(inc, 2),
                          "vcpu": n * r.get("cpu", 0), "ram_gb": n * r.get("ram_gb", 0), "disco_gb": n * r.get("disco_gb", 0)})
            total += mensual
            incremental += inc
    return {"moneda": t.get("moneda", "USD"), "filas": filas, "mensual": round(total, 2), "anual": round(total * 12, 2),
            "incremental": round(incremental, 2), "vcpu": sum(f["vcpu"] for f in filas), "ram_gb": sum(f["ram_gb"] for f in filas),
            "disco_gb": sum(f["disco_gb"] for f in filas), "servidores": sum(f["replicas"] for f in filas if f["uso"] == "dedicado" and f["sistema"]),
            "tarifas": cen}
