"""Skills de la máquina: cada habilidad con contexto propio sobre la máquina (apagar, encender, inventariar, mover, cambiar
recursos, respaldo, certificado, parches, eliminar…) que se lanza desde el panel «Skills» de su ficha.

Cada skill es una automatización del catálogo que crea un ticket (con aprobación si la automatización la pide). Las que
tienen endpoint propio en la ficha (energía, inventariar, eliminar, mover a otra infraestructura, CPU, memoria, discos) usan sus
mismos endpoints; el resto se lanza con «ejecutar_en_equipo». Qué automatizaciones aplican lo decide la misma regla de la
ficha (operacion.aplica_a_equipo: sistema, tipo, red, skills instaladas, inventario de AAP y estado). Las que aplican pero
aún no pasan su plan de prueba (regla de prueba) se muestran deshabilitadas con el motivo."""
import os
import re

from flask import g, url_for

from .bd import j

# Bandera: los botones «Skills» de la ficha (máquina y plataformas) están apagados por ahora; la ficha muestra sus botones de
# siempre (energía en la caja de estado, CPU/memoria/discos en su caja, encender/apagar en cada plataforma). FACEOPS_SKILLS=1 los vuelve a mostrar.
ACTIVAS = os.environ.get("FACEOPS_SKILLS", "0") == "1"

GRUPOS = [("energia", "Energía"), ("inventario", "Inventario y diagnóstico"), ("recursos", "Recursos y movimiento"),
          ("seguridad", "Seguridad"), ("respaldo", "Respaldo y recuperación"), ("plataformas", "Plataformas"), ("ciclo", "Ciclo de vida")]

_R_ENERGIA = r"\b(encender|apagar|reiniciar)\b|energ[ií]a"
_R_SEGURIDAD = r"segurid|certificad|\bcve|parche|escane|vulnerab|endurec|harden|pol[ií]tica|selinux|cumplim|drift|amenaza|b[oó]veda|antivirus"
_R_RESPALDO = r"respald|backup|restaur|recuper|punto de restauraci|snapshot"
_R_RECURSOS = r"recurso|vcpu|memoria|\bdisco|volumen|mover|traslad|ampliar|dimension|migra"
_R_INVENTARIO = r"inventar|recolect|verific|estado|reporte|consult|listar|audit|revis|relev|diagn|evalu|homolog|monitore|alerta|m[eé]trica|\blogs?\b"


def _texto(a):
    return f"{a.get('nombre_aap') or ''} {a.get('nombre') or ''}".lower()


def lectura(a):
    from .operacion import PALABRAS_LECTURA
    return (a.get("tipo") or "") == "lectura" or any(p in _texto(a) for p in PALABRAS_LECTURA)


def grupo_de(a, plataforma=False):
    """Contexto de la skill (en qué grupo del panel va)."""
    from .areas import area_de
    t = _texto(a)
    if re.search(_R_ENERGIA, t):
        return "energia"
    if plataforma:
        return "plataformas"
    if area_de(a) == "seguridad" or re.search(_R_SEGURIDAD, t):
        return "seguridad"
    if re.search(_R_RESPALDO, t):
        return "respaldo"
    if (a.get("categoria") or "") == "Migraciones" or (a.get("nombre_aap") or "").lower().startswith("migración ·"):
        return "ciclo" if "sistema operativo" in t else "recursos"
    if re.search(_R_RECURSOS, t):
        return "recursos"
    if lectura(a) or re.search(_R_INVENTARIO, t):
        return "inventario"
    return "ciclo"


_AMB = {}


def ambito(nombre_aap):
    """«so» o la lista de familias de plataforma de la automatización, según datos/skills-ambito.yml (explícito por nombre,
    luego por prefijo «X · …»; lo que no figure queda en el sistema operativo y se informa en sin_ambito())."""
    import os
    import yaml
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "skills-ambito.yml")
    m = os.path.getmtime(ruta) if os.path.exists(ruta) else 0
    if _AMB.get("m") != m:
        _AMB.update(m=m, c=(yaml.safe_load(open(ruta, encoding="utf-8")) if m else None) or {}, sin=set())
    c = _AMB["c"]
    v = (c.get("automatizaciones") or {}).get(nombre_aap)
    if v is None:
        v = (c.get("prefijos") or {}).get(nombre_aap.split(" · ")[0])
    if v is None:
        _AMB["sin"].add(nombre_aap)
        return "so"
    return "so" if v == "so" else [str(x) for x in (v if isinstance(v, list) else [v])]


def sin_ambito():
    return sorted(_AMB.get("sin") or [])


def nombre_so(e):
    """Rótulo corto del sistema de la máquina para el panel: «RHEL 9.8», «Windows Server 2022», «VyOS»…"""
    so = (e.get("so") or "").strip()
    m = re.search(r"(?:red ?hat(?: enterprise linux)?|rhel)[^0-9]*([0-9]+(?:\.[0-9]+)?)", so, re.I)
    if m:
        return f"RHEL {m.group(1)}"
    m = re.search(r"windows server (\d{4})", so, re.I)
    if m:
        return f"Windows Server {m.group(1)}"
    if (e.get("categoria") or "") == "clúster openshift":
        return so or "OpenShift"
    return re.sub(r"\s+\S*\d{4}[.-]\d{2}[.-]?\d*\S*", "", so).strip() or (e.get("categoria") or "Sistema").capitalize()


def _motivo_bloqueo(a):
    from .pruebas import estado
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    return {"cambiada": "cambió desde su última prueba y debe probarse de nuevo",
            "fallida": "su última prueba falló y debe pasar de nuevo"}.get(ep, "aún no tiene su plan de prueba ejecutado y aprobado")


def _que(a):
    t = (j(a.get("guia"), {}) or {}).get("proposito") or a.get("descripcion") or ""
    t = re.split(r"(?<=[.;:])\s", t.strip(), 1)[0].rstrip(".;:")
    return t[:170] + ("…" if len(t) > 170 else "")


def armar(e, acciones, de_comp, so_cod, energia_ok, puede_eliminar, recursos_ok, mover_ok, escritura, lim_rec=None, componentes=(), desinstalando=None):
    """Grupos con sus skills para el panel. acciones: automatizaciones del catálogo que aplican (ya certificadas)."""
    from .vistas import AGREGAR_DISCO, ELIMINAR, ENERGIA, CATEGORIAS_RED
    from .operacion import aplica_a_equipo
    from .inventariado import AUTOMATIZACION as INVENTARIAR
    from .bd import q
    from .pruebas import no_certificadas
    bloq = no_certificadas(g.cliente["id"])
    # las de flujo propio (energía, eliminar…) pueden no estar publicadas en el catálogo: se buscan entre todas las del cliente
    todas = {a["nombre_aap"]: dict(a, _bloqueada=a["id"] in bloq) for a in q("SELECT * FROM automatizaciones WHERE cliente_id=?", g.cliente["id"])}
    visibles = [a for a in todas.values() if a.get("visible")]
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    cat = (e.get("categoria") or "servidor")
    est = e.get("energia") or ""
    out = []

    def base(clave, grupo, nombre, que, nombre_aap, **kw):
        a = todas.get(nombre_aap)
        s = {"id": clave, "grupo": grupo, "nombre": nombre, "que": que, "aap": nombre_aap, "aid": a["id"] if a else None,
             "aprob": bool(a and a.get("requiere_aprobacion")), "lectura": False, "bloqueo": "", "deshab": "", "campos": [],
             "confirmar": "", "destructiva": False, "url": "", "datos": {}, "abrir": "", "tipo": "ticket", "plats": []}
        s.update(kw)
        if not a:
            s["bloqueo"] = f"Falta «{nombre_aap}» en el catálogo (sincroniza con AAP)"
        elif a.get("_bloqueada"):
            s["bloqueo"] = f"Bloqueada por la regla de prueba: «{a['nombre']}» {_motivo_bloqueo(a)}"
        out.append(s)
        return s

    propios = set()
    # ── Energía: los mismos botones de antes (endpoint equipo_energia)
    if energia_ok:
        clu = " Se aplica a todos los nodos del clúster." if cat == "clúster openshift" else ""
        prod = " Es producción: pasa por aprobación en AAP." if e.get("ambiente") == "produccion" else ""
        for ac, et, q_ in (("encender", "Encender", "Enciende la máquina virtual en su anfitrión."),
                           ("reiniciar", "Reiniciar", "Reinicia el sistema de forma ordenada."),
                           ("apagar", "Apagar", "Apaga el sistema de forma ordenada (no la retira).")):
            s = base("energia-" + ac, "energia", et, q_, ENERGIA, url=url_for("consola.equipo_energia", eid=e["id"]), datos={"accion": ac},
                     confirmar=f"¿{et} {e['nombre']}?{clu}{prod}")
            s["aprob"] = s["aprob"] or e.get("ambiente") == "produccion"
            if ac == "encender" and est == "encendido":
                s["deshab"] = "La máquina ya está encendida"
            elif ac != "encender" and est in ("apagado", "suspendido"):
                s["deshab"] = "La máquina está apagada"
        propios.add(ENERGIA)
    elif escritura and (cat == "clúster openshift" or d.get("nodo_ocp")):
        for ac, et in (("encender", "Encender"), ("reiniciar", "Reiniciar"), ("apagar", "Apagar")):
            base("energia-" + ac, "energia", et, "Energía de la máquina.", ENERGIA,
                 deshab="Aún no disponible: los nodos de este clúster no están en un hipervisor que FaceOps controle")
        propios.add(ENERGIA)
    # ── Inventario: el mismo del botón de antes (endpoint equipo_inventariar), con la acción que corresponde al tipo
    if escritura and e.get("ip"):
        inv = ("OpenShift · Recolectar clúster" if cat == "clúster openshift" or d.get("nodo_ocp")
               else "Red · Inventariar equipo de red" if cat in CATEGORIAS_RED else INVENTARIAR)
        s = base("inventariar", "inventario", "Inventariar", "Trae sistema, servicios y paquetes; FaceOps los contrasta con lo que sabe de la máquina.",
                 inv, url=url_for("consola.equipo_inventariar", eid=e["id"]), lectura=True)
        propios.update({inv, INVENTARIAR, "OpenShift · Recolectar clúster", "Red · Inventariar equipo de red"})
    # ── Recursos y movimiento: CPU, memoria y discos con su formulario (mismos endpoints de la ficha); mover abre su flotante
    lr = lim_rec or {}
    libre = lr.get("disco_disp_gb")
    cap_txt = f" · disco libre en el anfitrión {libre} GB" if libre is not None else ""
    if recursos_ok and lr:
        cur_c, cur_r = int(e.get("cpu") or 0), round((e.get("ram_mb") or 0) / 1024)
        anf = f"{lr.get('infra')}" + (f" ({lr['cores']} cores físicos, {round(lr.get('ram_disp_gb') or 0)} GB libres)" if lr.get("cores") else "")
        for k, nombre, que, campo, ops, cur, otro, unidad, mx in (
                ("recursos-cpu", "Cambiar CPU (vCPU)", "Sube o baja los vCPU; si está encendida se apaga ordenadamente, se ajusta y se enciende.",
                 "cpu", lr.get("cpus") or [], cur_c, {"ram": cur_r}, "vCPU", lr.get("max_cpu")),
                ("recursos-ram", "Cambiar memoria", "Sube o baja la memoria; si está encendida se apaga ordenadamente, se ajusta y se enciende.",
                 "ram", lr.get("rams") or [], cur_r, {"cpu": cur_c}, "GB", lr.get("max_ram"))):
            ops_ = sorted(set(ops) | ({cur} if cur else set()))
            base(k, "recursos", nombre, que, "Infra · Ajustar recursos de máquinas", tipo="form", crudo=True,
                 url=url_for("consola.equipo_recursos", eid=e["id"]), datos=otro, actual={campo: str(cur)},
                 campos=[{"var": campo, "etiqueta": f"Nuevo valor ({unidad})", "tipo": "multiplechoice", "requerido": True, "defecto": str(cur),
                          "opciones": [str(n) for n in ops_],
                          "ayuda": f"Actual: {cur} {unidad} · máximo para esta máquina: {mx} {unidad} en {anf}"}])
        propios.add("Infra · Ajustar recursos de máquinas")
    if mover_ok:
        base("mover", "recursos", "Mover a otra infraestructura", "Traslada la máquina a otro hipervisor o centro de datos (la de origen queda de respaldo).",
             "Infra · Mover máquina entre centros de datos", tipo="abrir", abrir="mover")
        propios.add("Infra · Mover máquina entre centros de datos")
    if energia_ok and e.get("ip"):
        win = "windows" in (e.get("so") or "").lower()
        base("disco-nuevo", "recursos", "Incorporar un disco", "Crea un volumen en el pool del hipervisor, lo conecta en caliente y lo deja con formato y montado.",
             AGREGAR_DISCO, tipo="form", crudo=True, url=url_for("consola.equipo_disco", eid=e["id"]), libre=libre, disco_nuevo=True,
             datos={"origen": "hipervisor", "pool": "images", "formatear": "1"},
             campos=[{"var": "gb", "etiqueta": "Tamaño (GB)", "tipo": "multiplechoice", "requerido": True, "defecto": "20",
                      "opciones": [str(n) for n in (10, 20, 50, 100, 200, 500) if libre is None or n + 5 <= libre], "ayuda": "Pool images del hipervisor" + cap_txt},
                     {"var": "nombre", "etiqueta": "Nombre del volumen", "tipo": "text", "requerido": True, "defecto": "datos", "ayuda": "minúsculas, números y guiones"},
                     ({"var": "letra", "etiqueta": "Letra de unidad", "tipo": "multiplechoice", "requerido": True, "defecto": "E", "opciones": list("EFGHIJ")} if win else
                      {"var": "montaje", "etiqueta": "Punto de montaje", "tipo": "text", "requerido": True, "defecto": "/datos"})])
        dks = [x for x in j(e.get("discos"), []) if x.get("dev")]
        if not win and dks:
            mnts = [m.get("punto") for x in dks for pt in (x.get("particiones") or []) for m in (pt.get("montajes") or []) if m.get("punto")]
            base("disco-ampliar", "recursos", "Ampliar un disco", "Agranda el disco en caliente en el anfitrión y crece su partición, LVM y sistema de archivos (no se achica).",
                 "FaceOps · Ampliar disco", tipo="form", crudo=True, url=url_for("consola.equipo_disco_ampliar", eid=e["id"]), libre=libre,
                 actuales={x["dev"]: x.get("gb") or 0 for x in dks},
                 campos=[{"var": "disco", "etiqueta": "Disco", "tipo": "multiplechoice", "requerido": True, "defecto": dks[0]["dev"],
                          "opciones": [x["dev"] for x in dks], "ayuda": "Actual: " + " · ".join(f"{x['dev']} {x.get('gb')} GB" for x in dks) + cap_txt},
                         {"var": "gb", "etiqueta": "Tamaño nuevo (GB)", "tipo": "integer", "requerido": True,
                          "defecto": str(int((dks[0].get("gb") or 0) * 2) or 20), "ayuda": "Debe ser mayor que el actual del disco elegido"},
                         {"var": "montaje", "etiqueta": "Montaje que crece", "tipo": "multiplechoice" if mnts else "text", "requerido": False,
                          "defecto": "/" if "/" in mnts else (mnts[0] if mnts else ""), "opciones": list(dict.fromkeys(mnts))}])
        propios.update({AGREGAR_DISCO, "FaceOps · Ampliar disco"})
    # ── Respaldo y recuperación: puntos de restauración (sección propia de la ficha)
    if cat not in CATEGORIAS_RED and escritura:
        out.append({"id": "puntos", "grupo": "respaldo", "nombre": "Puntos de restauración", "que": "Crea un punto ahora o ajusta la frecuencia automática.",
                    "aap": "", "aid": None, "aprob": False, "lectura": False, "bloqueo": "", "deshab": "", "campos": [], "confirmar": "",
                    "destructiva": False, "url": "", "datos": {}, "abrir": "puntos", "tipo": "abrir", "plats": []})
        out.append({"id": "recuperar", "grupo": "respaldo", "nombre": "Recuperar desde un punto", "que": "Elige el punto de restauración y pide la recuperación (con aprobación).",
                    "aap": "", "aid": None, "aprob": True, "lectura": False, "bloqueo": "", "deshab": "", "campos": [], "confirmar": "",
                    "destructiva": False, "url": "", "datos": {}, "abrir": "recuperar", "tipo": "abrir", "plats": []})
    # ── Ciclo de vida: eliminar (endpoint eliminar_equipo)
    if puede_eliminar:
        base("eliminar", "ciclo", "Eliminar máquina", "La saca del balanceo, la apaga, la retira del hipervisor y de AAP; disco y Git se conservan.",
             ELIMINAR, url=url_for("consola.eliminar_equipo", eid=e["id"]), destructiva=True,
             confirmar=f"¿Eliminar {e['nombre']}? Se apaga, se retira del hipervisor y sale de los inventarios de AAP. El disco y su respaldo en Git se conservan para poder recuperarlo.")
        propios.add(ELIMINAR)

    # ── Plataformas: energía del servicio (Encender/Reiniciar/Apagar), operadores y desinstalar; cada una ligada a su plataforma
    from .vistas import SERVICIOS_PLATAFORMA, desinstalable, familia, servicio_estado
    propios.update({"Plataforma · Controlar servicio", "Plataforma · Desinstalar", "OpenShift · Operador"})
    for c in componentes if escritura else ():
        if c.get("so"):
            continue
        cod, nom = c["codigo"], c["nombre"]
        if c.get("op"):
            for ac, et, q_, des in (("reiniciar", "Reiniciar", "Reinicia los pods del operador.", False),
                                    ("desinstalar", "Desinstalar", "Borra su suscripción y su CSV del clúster.", True)):
                base(f"op-{ac}-{cod}", "energia" if ac == "reiniciar" else "ciclo", f"{et} {nom}", q_, "OpenShift · Operador", plats=[cod],
                     url=url_for("consola.equipo", eid=e["id"]) + f"/operador/{cod}", datos={"accion": ac}, destructiva=des,
                     confirmar=f"¿{et} el operador {nom}?")
            continue
        if c.get("nodo"):
            continue
        if familia(cod) in SERVICIOS_PLATAFORMA:
            st = servicio_estado(e, cod)
            for ac, et, q_ in (("encender", "Encender", "Levanta el servicio de la plataforma (no toca la máquina)."),
                               ("reiniciar", "Reiniciar", "Reinicia el servicio de la plataforma."),
                               ("apagar", "Apagar", "Detiene el servicio de la plataforma (no toca la máquina).")):
                sk = base(f"srv-{ac}-{cod}", "energia", f"{et} {nom}", q_, "Plataforma · Controlar servicio", plats=[cod],
                          url=url_for("consola.plataforma_servicio", eid=e["id"], cod=cod), datos={"accion": ac, "nombre": nom},
                          confirmar=f"¿{et} {nom} en {e['nombre']}?")
                if ac == "encender" and st == "en ejecución":
                    sk["deshab"] = f"{nom} ya está en ejecución"
                elif ac != "encender" and st == "detenido":
                    sk["deshab"] = f"{nom} está detenido"
        if desinstalable(cod):
            dz = (desinstalando or {}).get(familia(cod))
            sk = base(f"desinstalar-{cod}", "ciclo", f"Desinstalar {nom}", "Detiene su servicio y quita sus paquetes (o su contenedor).", "Plataforma · Desinstalar",
                      plats=[cod], url=url_for("consola.equipo", eid=e["id"]) + f"/plataforma/{cod}/desinstalar", datos={"nombre": nom}, destructiva=True,
                      confirmar=f"¿Desinstalar {nom} de {e['nombre']}? Se detiene su servicio y se quitan sus paquetes.")
            if dz:
                sk["deshab"] = f"En desinstalación: {dz['numero']}"

    # ── Catálogo: todas las automatizaciones que aplican a la máquina (las bloqueadas por la regla de prueba, deshabilitadas)
    vistos = set(propios)
    cand = list(acciones) + [a for a in visibles if a.get("_bloqueada") and aplica_a_equipo(g.usuario, g.cliente["id"], a, e)]
    for a in cand:
        if a["nombre_aap"] in vistos or a.get("habilita_skill"):
            continue   # instalar plataformas y agentes: «Incorporar plataforma» y la sección Agentes
        vistos.add(a["nombre_aap"])
        amb = ambito(a["nombre_aap"])
        if amb == "so":
            comps = []
        else:   # skill de plataforma: solo si la máquina la tiene instalada (y no es su propio sistema, como OpenShift en un clúster)
            comps = [c["codigo"] for c in componentes if familia(c["codigo"]) in amb and not c.get("so")]
            if not comps:
                if any(c.get("so") and familia(c["codigo"]) in amb for c in componentes):
                    comps = []
                else:
                    continue
        campos = [c for c in j(a.get("campos"), []) if c.get("tipo") not in ("equipo", "equipos")]
        s = {"id": f"a{a['id']}", "plats": comps, "grupo": grupo_de(a), "nombre": a["nombre"], "que": _que(a), "aap": a["nombre_aap"], "aid": a["id"],
             "aprob": bool(a.get("requiere_aprobacion")), "lectura": lectura(a), "deshab": "", "campos": campos,
             "bloqueo": f"Bloqueada por la regla de prueba: {_motivo_bloqueo(a)}" if a.get("_bloqueada") else "",
             "confirmar": "", "destructiva": bool(re.search(r"eliminar|desinstalar|borrar|retirar", _texto(a))),
             "url": url_for("consola.ejecutar_en_equipo", eid=e["id"], aid=a["id"]), "datos": {}, "abrir": "",
             "tipo": "form" if campos else "ticket"}
        out.append(s)
    grupos = []
    for k, n in GRUPOS:
        xs = [s for s in out if s["grupo"] == k]
        if xs:
            grupos.append({"clave": k, "nombre": n, "skills": xs if k == "energia" else
                           sorted(xs, key=lambda s: (bool(s["bloqueo"]), bool(re.fullmatch(r"a\d+", s["id"])), s["nombre"].lower()))})
    maq = [s for s in out if not s.get("plats")]
    por_plat = {}
    for s in out:
        for c in s.get("plats") or []:
            por_plat[c] = por_plat.get(c, 0) + (0 if s["bloqueo"] else 1)
    return {"grupos": grupos, "total": len(out), "bloqueadas": sum(1 for s in out if s["bloqueo"]), "por_plat": por_plat,
            "maquina": sum(1 for s in maq if not s["bloqueo"]), "maquina_total": len(maq), "so_nombre": nombre_so(e),
            "plat_nombre": {c["codigo"]: c["nombre"] for c in componentes if not c.get("so")}}
