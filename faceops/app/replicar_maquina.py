"""Replicar máquina (flotante de salud › Disponibilidad de la ficha de la máquina).

Una réplica es una máquina PARALELA, no un clon: «Habilitar servidor» la crea desde la imagen base del sistema (machine-id,
llaves SSH de host, hostname, UUID, MAC, IP y registro en Red Hat propios) y «Infra · Replicar máquina · configurar» le pone
lo que hace al rol del origen (plataforma, configuración y despliegues, agente de monitoreo, políticas al día), la suma al
balanceador y abre el firewall de cada segmento donde está el origen, y verifica HTTP 200 por cada balanceador. Si algo
falla, el mismo workflow la retira del balanceo y elimina la VM nueva. Todo va en UN ticket con aprobación en AAP.

FaceOps guarda la relación origen → réplica (tabla replicas_maquina): con ella la Disponibilidad de ambas cuenta las dos
instancias y la réplica entra en el componente del segmento que ocupa el origen."""
import json
import re

from .bd import ahora, auditar, ej, j, q, q1

AUTO = "Infra · Replicar máquina"
PRODUCTOS = {"jboss": 8080, "nginx": 80}   # plataformas sin estado que se replican tal cual detrás de un balanceador
_ok_tabla = set()


def _tabla():
    from .bd import con
    k = id(con())
    if k in _ok_tabla:
        return
    con().executescript("""CREATE TABLE IF NOT EXISTS replicas_maquina (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, origen TEXT NOT NULL, replica TEXT NOT NULL, ticket TEXT DEFAULT '',
      fecha TEXT, UNIQUE(cliente_id, replica));""")
    _ok_tabla.add(k)


def replicas(cliente_id):
    """{réplica: origen} de las réplicas vigentes (las retiradas no cuentan)."""
    _tabla()
    vivos = {r["nombre"] for r in q("SELECT nombre FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente_id)}
    return {r["replica"]: r["origen"] for r in q("SELECT origen, replica FROM replicas_maquina WHERE cliente_id=?", cliente_id)
            if r["replica"] in vivos}


def relacionadas(cliente_id, nombre):
    """Las máquinas que sirven lo mismo que «nombre» por ser su origen o sus réplicas (sin incluirla)."""
    rep = replicas(cliente_id)
    raiz = rep.get(nombre, nombre)
    return ({raiz} | {r for r, o in rep.items() if o == raiz}) - {nombre}


def _skills(e):
    from .inventario import skills_de
    return set(e.get("skills") or skills_de(e))


def _so(e):
    if (e.get("so_familia") or "") != "RedHat" and "red hat" not in (e.get("so") or "").lower():
        return None
    v = str(e.get("so_version") or "").split(".")[0]
    return f"rhel{v}" if v in ("8", "9", "10") else None


def segmentos_maquina(cliente, e, eqs):
    """Segmentos de negocio que usan la máquina: el componente que ocupa, su ambiente y, desde la arquitectura vigente,
    los balanceadores que le entregan tráfico, los puertos que recibe y los destinos a los que se conecta."""
    from .arquitectura import asignacion, vigentes
    from .inventario import segmentos_de
    out, vistos = [], set()
    for seg, r in vigentes(cliente["id"]).items():
        m = r["m"]
        asg = asignacion(m, eqs)
        comps = {c["id"]: c for c in m["componentes"]}
        for cid, por_amb in asg.items():
            for amb, xs in por_amb.items():
                if e["nombre"] not in [x["nombre"] for x in xs]:
                    continue
                c = comps[cid]
                d = {"nombre": seg, "codigo": m.get("codigo", ""), "componente": c.get("nombre", cid), "comp_id": cid, "ambiente": amb,
                     "arquitectura": True, "balanceadores": [], "entradas": [], "destinos": [], "url": ""}
                ent = m.get("entrada") or {}
                for f in m["flujos"]:
                    if f["hacia"] == cid:
                        d["entradas"] += [int(p) for p in f["puertos"]]
                        cd = comps.get(f["desde"])
                        if cd and (cd.get("producto") == "nginx" or cd.get("tipo") == "balanceador"):
                            for b in asg.get(f["desde"], {}).get(amb, []):
                                if b.get("ip") and b["nombre"] != e["nombre"]:
                                    url = ""
                                    if ent.get("hacia") == f["desde"] and ent.get("url"):
                                        url = ent["url"]
                                    d["balanceadores"].append({"segmento": seg, "nombre": b["nombre"], "ip": b["ip"], "url": url})
                    elif f["desde"] == cid:
                        for b in asg.get(f["hacia"], {}).get(amb, []):
                            if b.get("ip") and b["nombre"] != e["nombre"]:
                                d["destinos"].append({"nombre": b["nombre"], "ip": b["ip"], "puertos": [int(p) for p in f["puertos"]],
                                                      "componente": comps.get(f["hacia"], {}).get("nombre", f["hacia"])})
                d["entradas"] = sorted(set(d["entradas"]))
                out.append(d)
                vistos.add(seg)
    segs_e = e.get("segmentos") or segmentos_de(e, q("SELECT * FROM segmentos WHERE cliente_id=?", cliente["id"]))
    for s in segs_e:
        if s not in vistos:   # segmento sin arquitectura vigente: se muestra, pero no aporta balanceo ni puertos
            out.append({"nombre": s, "codigo": "", "componente": "", "comp_id": "", "ambiente": e.get("ambiente") or "", "arquitectura": False,
                        "balanceadores": [], "entradas": [], "destinos": [], "url": ""})
    return out


def siguiente_nombre(cliente_id, nombre):
    """El siguiente nombre libre de la familia (nodo-jboss → nodo-jboss2, 3…; srv-x-01 → srv-x-02…), sin repetir ninguno
    que FaceOps haya conocido (incluidas las máquinas retiradas: su disco puede seguir en el anfitrión)."""
    _tabla()
    usados = {r["nombre"] for r in q("SELECT nombre FROM equipos WHERE cliente_id=?", cliente_id)}
    usados |= {r["replica"] for r in q("SELECT replica FROM replicas_maquina WHERE cliente_id=?", cliente_id)}
    m = re.fullmatch(r"(.*?)([-_]?)(\d+)", nombre)
    base, sep, ancho = (m.group(1), m.group(2), len(m.group(3))) if m else (nombre, "", 1)
    for i in range(2, 100):
        n = f"{base}{sep}{str(i).zfill(ancho)}"
        if n not in usados and n != nombre:
            return n
    return ""


def _cupo_aap(cliente):
    """Nodos libres de la licencia de AAP (cada máquina automatizada cuenta uno)."""
    try:
        from .aap import AAP
        li = (AAP.de_cliente(cliente).get("config/") or {}).get("license_info") or {}
    except Exception:  # noqa: BLE001
        return None
    if li.get("instance_count") is None:
        return None
    if li.get("free_instances") is not None:
        return int(li["free_instances"])
    return int(li["instance_count"]) - int(li.get("automated_instances") or li.get("current_instances") or 0)


def _politicas(cliente_id, e):
    """Una sola política con las reglas de las que el origen tiene al día (FaceOps · Aplicar política de seguridad)."""
    try:
        from .politicas_seg import vigentes
    except Exception:  # noqa: BLE001
        return None
    al_dia = {r["codigo"] for r in q("SELECT codigo FROM politicas_estado WHERE cliente_id=? AND equipo_id=? AND estado='al_dia'",
                                     cliente_id, e["id"])}
    pols = [p for p in vigentes(cliente_id) if p["codigo"] in al_dia]
    if not pols:
        return None
    return {"id": "replica-" + e["nombre"], "version": 1, "nombre": "Políticas al día en " + e["nombre"],
            "reglas": [r_ for p in pols for r_ in (p.get("reglas") or [])],
            "recargar": sorted({x for p in pols for x in (p.get("recargar") or [])}),
            "codigos": [p["codigo"] for p in pols], "versiones": {p["codigo"]: p["version"] for p in pols}}


def plan(cliente, e, eqs):
    """Lo que se haría al replicar la máquina (o por qué no se puede)."""
    prod = next((p for p in PRODUCTOS if p in _skills(e)), None)
    so = _so(e)
    segs = segmentos_maquina(cliente, e, eqs)
    bals, vistos = [], set()
    for s in segs:
        for b in s["balanceadores"]:
            if b["ip"] not in vistos:
                vistos.add(b["ip"])
                bals.append(b)
    destinos = {}
    for s in segs:
        for d in s["destinos"]:
            x = destinos.setdefault(d["ip"], {"nombre": d["nombre"], "ip": d["ip"], "puertos": []})
            x["puertos"] = sorted(set(x["puertos"]) | set(d["puertos"]))
    entradas = sorted({p for s in segs for p in s["entradas"]}) or ([PRODUCTOS[prod]] if prod else [])
    nube = "cloud" in (e.get("ubicacion") or "").lower() or (e.get("proveedor") or "").lower() not in ("kvm", "")
    por_que = ("la máquina no tiene una plataforma replicable detrás de un balanceador (JBoss o Nginx)" if not prod
               else "solo se replican máquinas RHEL 8, 9 o 10 (la réplica nace de la imagen base del mismo sistema)" if not so
               else "solo se replican máquinas del centro de datos on-premise (KVM)" if nube
               else "la máquina no tiene IP" if not e.get("ip")
               else "está apagada: enciéndela antes de replicarla" if (e.get("energia") or "") in ("apagado", "suspendido")
               else "ningún segmento tiene un balanceador que le entregue tráfico (revisa la arquitectura del segmento)" if not bals
               else "")
    discos = j(e.get("discos"), []) if isinstance(e.get("discos"), str) else (e.get("discos") or [])
    raiz = next((d for d in discos if any(mo.get("punto") == "/" for p in d.get("particiones") or [] for mo in p.get("montajes") or [])), None)
    rec = {"cpu": max(1, min(8, int(e.get("cpu") or 1))), "ram_gb": max(2, min(32, round((e.get("ram_mb") or 2048) / 1024))),
           "disco_gb": max(20, min(200, int(round((raiz or (discos[0] if discos else {})).get("gb") or 20))))}
    return {"posible": not por_que, "por_que": por_que, "producto": prod, "puerto": PRODUCTOS.get(prod), "so": so,
            "segmentos": segs, "balanceadores": bals, "destinos": list(destinos.values()), "entradas": entradas,
            "nombre": siguiente_nombre(cliente["id"], e["nombre"]), "recursos": rec, "politica": _politicas(cliente["id"], e),
            "instancias": 1 + len(relacionadas(cliente["id"], e["nombre"]))}


def _ruta(e, segs):
    """Ruta HTTP con la que se verifica la réplica: la de salud de la entrada del segmento (/app-jboss/salud) o «/»."""
    for s in segs:
        for b in s["balanceadores"]:
            u = b.get("url") or ""
            if u.count("/") >= 3:
                r = "/" + u.split("/", 3)[3]
                return r.rstrip("/") + "/salud" if r.rstrip("/") else "/"
    return "/"


def en_curso(cliente_id, nombre):
    return q1("SELECT t.numero FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND a.nombre_aap=? "
              "AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') AND t.parametros LIKE ?",
              cliente_id, AUTO, f'%"rep_origen": "{nombre}"%')


def solicitar_replica(usuario, cliente, e, eqs, form, prueba=False, extra=None):
    """UN ticket con aprobación en AAP: crea la máquina paralela, le pone el rol del origen, balanceo y firewall de cada segmento."""
    from .operacion import solicitar
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO)
    if not a:
        raise ValueError(f"Falta «{AUTO}» en el catálogo (sincroniza con AAP)")
    p = plan(cliente, e, eqs)
    if not p["posible"]:
        raise ValueError(f"No se puede replicar {e['nombre']}: {p['por_que']}")
    ya = en_curso(cliente["id"], e["nombre"])
    if ya:
        raise ValueError(f"Ya hay una réplica de {e['nombre']} en curso ({ya['numero']})")
    nombre = (form.get("nombre") or p["nombre"]).strip().lower()
    _tabla()
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,40}", nombre):
        raise ValueError("Nombre de la réplica no válido (3 a 41 letras minúsculas, números o guiones)")
    if nombre != p["nombre"] and not (extra or {}).get("_permitir_nombre") and \
            (q1("SELECT 1 FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
             or q1("SELECT 1 FROM replicas_maquina WHERE cliente_id=? AND replica=?", cliente["id"], nombre)):
        raise ValueError(f"{nombre} ya existe: la réplica necesita un nombre nuevo (sugerido: {p['nombre']})")
    cupo = _cupo_aap(cliente)
    if cupo is not None and cupo < 1 and not (extra or {}).get("_permitir_nombre"):
        raise ValueError("La licencia de AAP no tiene nodos libres: la réplica sería un nodo más. Libera uno o amplía la suscripción")
    rec = p["recursos"]
    try:
        cpu = max(1, min(8, int(form.get("cpu") or rec["cpu"])))
        ram = max(2, min(32, int(form.get("ram_gb") or rec["ram_gb"])))
    except ValueError:
        raise ValueError("Recursos no válidos") from None
    segs_nombres = list(dict.fromkeys(s["nombre"] for s in p["segmentos"]))
    bals = [dict(b, url=(b["url"].split("/", 3)[0] + "//" + b["url"].split("/", 3)[2] + _ruta(e, p["segmentos"])) if b.get("url") else "")
            for b in p["balanceadores"]]
    params = {"hab_interno": True, "hab_so": p["so"], "hab_destino": "onpremise", "hab_nombre": nombre[:20], "hab_nombre_exacto": nombre,
              "hab_replicas": "1", "hab_cpu": str(cpu), "hab_ram_gb": str(ram), "hab_disco_gb": str(rec["disco_gb"]),
              "hab_ambiente": e.get("ambiente") or "desarrollo", "hab_ubicacion": e.get("ubicacion") or "Centro de datos Santiago",
              "hab_segmento": segs_nombres if len(segs_nombres) > 1 else (segs_nombres[0] if segs_nombres else ""),
              "rep_origen": e["nombre"], "rep_origen_ip": e["ip"], "rep_nombre": nombre, "rep_producto": p["producto"],
              "rep_puerto": p["puerto"], "rep_entradas": p["entradas"], "rep_balanceadores": bals, "rep_destinos": p["destinos"],
              "rep_ruta": _ruta(e, p["segmentos"]), "rep_politica": p["politica"] or {},
              "el_equipo": nombre, "el_balanceadores": [{"nombre": b["nombre"], "ip": b["ip"]} for b in bals]}
    params.update({k: v for k, v in (extra or {}).items() if not k.startswith("_")})
    motivo = (f"Replicar {e['nombre']} en {nombre} (máquina paralela, no clon) · balanceo y firewall en "
              + (", ".join(segs_nombres) or "su segmento") + (f" · {form.get('motivo').strip()}" if (form.get("motivo") or "").strip() else ""))
    t = solicitar(usuario, cliente, a, segs_nombres[0] if segs_nombres else None, params, motivo, prueba=prueba)
    auditar(usuario["usuario"], cliente["id"], "replicar máquina", t["numero"], {"origen": e["nombre"], "replica": nombre, "segmentos": segs_nombres})
    return t


def vuelta_atras(aap, t):
    """El workflow con vuelta atrás termina «successful» en AAP aunque la réplica se haya revertido: si no llegó la
    verificación final, el ticket queda fallido con lo que se deshizo."""
    a = q1("SELECT nombre_aap FROM automatizaciones WHERE id=?", t["automatizacion_id"]) or {}
    if a.get("nombre_aap") != AUTO:
        return None
    from .operacion import artefactos_ticket
    art = artefactos_ticket(aap, t) or {}
    if (art.get("faceops_replica") or {}).get("ok"):
        return None
    el = art.get("faceops_eliminado") or {}
    return ("\nLa réplica no quedó verificada: vuelta atrás ejecutada (se retiró del balanceo y se eliminó la máquina nueva"
            + (f": {', '.join(el.get('detalle') or [])}" if el else "") + "). El origen no se modificó.")


def registrar(aap, cliente, t):
    """Tras el ticket exitoso: relación origen → réplica, la réplica en el inventario de AAP del origen (grupos de su
    plataforma, para que los despliegues la alcancen) y las políticas aplicadas. Devuelve el texto para el ticket."""
    from .operacion import artefactos_ticket
    _tabla()
    pr = j(t["parametros"], {})
    art = artefactos_ticket(aap, t) or {}
    r = art.get("faceops_replica") or {}
    origen, rep = pr.get("rep_origen"), pr.get("rep_nombre")
    if not origen or not rep or not r.get("ok"):
        return ""
    ej("INSERT INTO replicas_maquina(cliente_id, origen, replica, ticket, fecha) VALUES (?,?,?,?,?) "
       "ON CONFLICT(cliente_id, replica) DO UPDATE SET origen=excluded.origen, ticket=excluded.ticket, fecha=excluded.fecha",
       cliente["id"], origen, rep, t["numero"], ahora())
    lineas = [f"Réplica {rep} ({r.get('ip')}) de {origen}: {r.get('producto')} responde {r.get('directo')} directo"
              + (f" ({r.get('version')})" if r.get("version") else "")
              + "; por balanceador: " + "; ".join(f"{k} → {', '.join(v)}" for k, v in (r.get("repartos") or {}).items())]
    eo = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], origen)
    try:   # la réplica también en el inventario de AAP del origen, en los grupos de su plataforma (despliegues y balanceo por grupo)
        inv = (aap.get("inventories/", name=eo["inventario"]).get("results") or [None])[0] if eo else None
        if inv and eo["inventario"] != "Servidores habilitados":
            hv = {"ansible_host": r.get("ip"), "faceops_ambiente": eo.get("ambiente") or "", "faceops_replica_de": origen,
                  "faceops_ticket": t["numero"]}
            h = (aap.get("hosts/", name=rep, inventory=inv["id"]).get("results") or [None])[0] or \
                aap.post("hosts/", {"name": rep, "inventory": inv["id"], "variables": json.dumps(hv)})
            gr = [x for x in j(eo["grupos"], []) if x == r.get("producto")]
            for gn in gr:
                g_ = (aap.get("groups/", name=gn, inventory=inv["id"]).get("results") or [None])[0]
                if g_:
                    try:
                        aap.post(f"groups/{g_['id']}/hosts/", {"id": h["id"]})
                    except Exception:  # noqa: BLE001 - ya estaba
                        pass
            lineas.append(f"{rep} en el inventario «{inv['name']}» de AAP" + (f" (grupo {', '.join(gr)})" if gr else ""))
            from .inventario import sincronizar
            sincronizar(cliente, "FaceOps")
    except Exception as x:  # noqa: BLE001 - la réplica ya está en «Servidores habilitados»
        lineas.append(f"No se pudo sumar al inventario del origen: {str(x)[:160]}")
    pol = pr.get("rep_politica") or {}
    er = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], rep)
    if pol.get("codigos") and er and rep in (art.get("faceops_politica") or {}):
        for cod in pol["codigos"]:
            ej("INSERT INTO politicas_estado(cliente_id, codigo, equipo_id, version, estado, modo, detalle, cuando, ticket_id) VALUES (?,?,?,?,?,?,?,?,?) "
               "ON CONFLICT(cliente_id, codigo, equipo_id) DO UPDATE SET version=excluded.version, estado=excluded.estado, modo=excluded.modo, "
               "detalle=excluded.detalle, cuando=excluded.cuando, ticket_id=excluded.ticket_id",
               cliente["id"], cod, er["id"], (pol.get("versiones") or {}).get(cod, 1), "al_dia", "aplicar", "[]", ahora(), t["id"])
        lineas.append("Políticas aplicadas: " + ", ".join(pol["codigos"]))
    auditar("FaceOps", cliente["id"], "réplica de máquina", t["numero"], {"origen": origen, "replica": rep, "resultado": r})
    return "\n".join(lineas)


def contexto(cliente, e, eqs, escritura):
    """Lo que muestra el flotante de salud › Disponibilidad: segmentos que usan la máquina y el botón «Replicar máquina»."""
    from flask import url_for
    try:
        p = plan(cliente, e, eqs)
    except Exception as x:  # noqa: BLE001 - la ficha se muestra igual
        return {"segmentos": [], "posible": False, "por_que": f"no se pudo armar el plan: {str(x)[:120]}"}
    for s in p["segmentos"]:
        s["link"] = url_for("consola.mapa", vista="aplicaciones", segmento=s["nombre"])
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], AUTO)
    bloqueo = ""
    if not a:
        bloqueo = f"falta «{AUTO}» en el catálogo"
    else:
        from .pruebas import estado as estado_prueba
        if estado_prueba(dict(a, cliente_id=cliente["id"]))["estado"] != "certificada":
            bloqueo = "falta su prueba"
    ya = en_curso(cliente["id"], e["nombre"])
    return dict(p, escritura=escritura, bloqueo=bloqueo, en_curso=ya["numero"] if ya else "", aid=a["id"] if a else None,
                url=url_for("consola.equipo_replicar", eid=e["id"]))
