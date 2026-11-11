"""Skills de la máquina: cada habilidad con contexto propio sobre la máquina (apagar, encender, inventariar, mover, cambiar
recursos, respaldo, certificado, parches, eliminar…) que se lanza desde el panel «Skills» de su ficha.

Cada skill es una automatización del catálogo que crea un ticket (con aprobación si la automatización la pide). Las que
tienen flujo propio en la ficha (energía, inventariar, eliminar, mover a otra infraestructura, recursos, discos) usan sus
mismos endpoints; el resto se lanza con «ejecutar_en_equipo». Qué automatizaciones aplican lo decide la misma regla de la
ficha (operacion.aplica_a_equipo: sistema, tipo, red, skills instaladas, inventario de AAP y estado). Las que aplican pero
aún no pasan su plan de prueba (regla de prueba) se muestran deshabilitadas con el motivo."""
import re

from flask import g, url_for

from .bd import j

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


def _motivo_bloqueo(a):
    from .pruebas import estado
    ep = estado(dict(a, cliente_id=g.cliente["id"]))["estado"]
    return {"cambiada": "cambió desde su última prueba y debe probarse de nuevo",
            "fallida": "su última prueba falló y debe pasar de nuevo"}.get(ep, "aún no tiene su plan de prueba ejecutado y aprobado")


def _que(a):
    t = (j(a.get("guia"), {}) or {}).get("proposito") or a.get("descripcion") or ""
    t = re.split(r"(?<=[.;:])\s", t.strip(), 1)[0].rstrip(".;:")
    return t[:170] + ("…" if len(t) > 170 else "")


def armar(e, acciones, de_comp, so_cod, energia_ok, puede_eliminar, recursos_ok, mover_ok, escritura):
    """Grupos con sus skills para el panel. acciones: automatizaciones del catálogo que aplican (ya certificadas)."""
    from .vistas import AGREGAR_DISCO, ELIMINAR, ENERGIA, CATEGORIAS_RED, autos_visibles
    from .operacion import aplica_a_equipo
    from .inventariado import AUTOMATIZACION as INVENTARIAR
    todas = {a["nombre_aap"]: a for a in autos_visibles(todas=True)}
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    cat = (e.get("categoria") or "servidor")
    est = e.get("energia") or ""
    out = []

    def base(clave, grupo, nombre, que, nombre_aap, **kw):
        a = todas.get(nombre_aap)
        s = {"id": clave, "grupo": grupo, "nombre": nombre, "que": que, "aap": nombre_aap, "aid": a["id"] if a else None,
             "aprob": bool(a and a.get("requiere_aprobacion")), "lectura": False, "bloqueo": "", "deshab": "", "campos": [],
             "confirmar": "", "destructiva": False, "url": "", "datos": {}, "abrir": "", "tipo": "ticket"}
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
        propios.add(inv)
    # ── Recursos y movimiento: flotantes propios de la ficha (recursos, discos, mover)
    if recursos_ok:
        base("recursos", "recursos", "Cambiar vCPU y memoria", "Ajusta los vCPU y la memoria dentro de lo que permite su anfitrión.",
             "Infra · Ajustar recursos de máquinas", tipo="abrir", abrir="recursos")
        propios.add("Infra · Ajustar recursos de máquinas")
    if mover_ok:
        base("mover", "recursos", "Mover a otra infraestructura", "Traslada la máquina a otro hipervisor o centro de datos (la de origen queda de respaldo).",
             "Infra · Mover máquina entre centros de datos", tipo="abrir", abrir="mover")
        propios.add("Infra · Mover máquina entre centros de datos")
    if energia_ok and e.get("ip"):
        base("disco-nuevo", "recursos", "Incorporar un disco", "Crea un volumen (hipervisor o storage), lo conecta y lo deja montado.",
             AGREGAR_DISCO, tipo="abrir", abrir="disco-nuevo")
        if "windows" not in (e.get("so") or "").lower() and any(x.get("dev") for x in j(e.get("discos"), [])):
            base("disco-ampliar", "recursos", "Ampliar un disco", "Agranda un disco en caliente y crece su partición, LVM y sistema de archivos.",
                 "FaceOps · Ampliar disco", tipo="abrir", abrir="disco")
        propios.update({AGREGAR_DISCO, "FaceOps · Ampliar disco"})
    # ── Respaldo y recuperación: puntos de restauración (sección propia de la ficha)
    if cat not in CATEGORIAS_RED and escritura:
        out.append({"id": "puntos", "grupo": "respaldo", "nombre": "Puntos de restauración", "que": "Crea un punto ahora o ajusta la frecuencia automática.",
                    "aap": "", "aid": None, "aprob": False, "lectura": False, "bloqueo": "", "deshab": "", "campos": [], "confirmar": "",
                    "destructiva": False, "url": "", "datos": {}, "abrir": "puntos", "tipo": "abrir"})
        out.append({"id": "recuperar", "grupo": "respaldo", "nombre": "Recuperar desde un punto", "que": "Elige el punto de restauración y pide la recuperación (con aprobación).",
                    "aap": "", "aid": None, "aprob": True, "lectura": False, "bloqueo": "", "deshab": "", "campos": [], "confirmar": "",
                    "destructiva": False, "url": "", "datos": {}, "abrir": "recuperar", "tipo": "abrir"})
    # ── Ciclo de vida: eliminar (endpoint eliminar_equipo)
    if puede_eliminar:
        base("eliminar", "ciclo", "Eliminar máquina", "La saca del balanceo, la apaga, la retira del hipervisor y de AAP; disco y Git se conservan.",
             ELIMINAR, url=url_for("consola.eliminar_equipo", eid=e["id"]), destructiva=True,
             confirmar=f"¿Eliminar {e['nombre']}? Se apaga, se retira del hipervisor y sale de los inventarios de AAP. El disco y su respaldo en Git se conservan para poder recuperarlo.")
        propios.add(ELIMINAR)

    # ── Catálogo: todas las automatizaciones que aplican a la máquina (las bloqueadas por la regla de prueba, deshabilitadas)
    vistos = set(propios)
    cand = list(acciones) + [a for a in todas.values() if a.get("_bloqueada") and aplica_a_equipo(g.usuario, g.cliente["id"], a, e)]
    for a in cand:
        if a["nombre_aap"] in vistos or a.get("habilita_skill"):
            continue   # instalar plataformas y agentes: «Incorporar plataforma» y la sección Agentes
        vistos.add(a["nombre_aap"])
        comps = [c for c in (de_comp(a) or []) if c and c != so_cod]
        campos = [c for c in j(a.get("campos"), []) if c.get("tipo") not in ("equipo", "equipos")]
        s = {"id": f"a{a['id']}", "grupo": grupo_de(a, bool(comps)), "nombre": a["nombre"], "que": _que(a), "aap": a["nombre_aap"], "aid": a["id"],
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
    return {"grupos": grupos, "total": len(out), "bloqueadas": sum(1 for s in out if s["bloqueo"])}
