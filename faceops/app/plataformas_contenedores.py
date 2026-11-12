"""Contenedores › «Crear plataforma de contenedores»: además de «Conectar existente», crear un clúster con una acción de AAP
(ticket y aprobación). Las opciones viven en datos/plataformas-contenedores.yml; la versión exacta de cada una es la última GA
de su rama mayor (canal stable de Red Hat, en caché una hora). Al terminar bien, el ticket registra el clúster como plataforma."""
import importlib.util
import os
import re
import threading
import time

import yaml
from flask import abort, flash, g, jsonify, redirect, request, url_for

from . import requiere_login
from .bd import q1
from .vistas import _escritura, bp

RAIZ = os.path.dirname(os.path.dirname(__file__))
TTL = 3600
_cache, _mutex = {}, threading.Lock()


def catalogo():
    return yaml.safe_load(open(os.path.join(RAIZ, "datos", "plataformas-contenedores.yml"), encoding="utf-8")) or {}


def _resolutor():
    sp = importlib.util.spec_from_file_location("ocp_gcp", os.path.join(RAIZ, "aap", "files", "ocp_gcp.py"))
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


def version_rama(rama):
    """{'version', 'canal'} o {'error', 'otros_canales'} para la rama mayor (caché de una hora; los errores, 5 minutos)."""
    with _mutex:
        c = _cache.get(rama)
        if c and time.time() - c[0] < (TTL if c[1].get("version") else 300):
            return c[1]
    try:
        r = _resolutor().version(f"ultima-{rama}")
    except Exception as x:  # noqa: BLE001 - sin red hacia Red Hat: la opción queda deshabilitada con el motivo
        r = {"error": f"no se pudo consultar las versiones de Red Hat ({type(x).__name__})"}
    with _mutex:
        _cache[rama] = (time.time(), r)
    return r


def opciones(cliente_id):
    """Opciones del selector con su versión resuelta y, si no se puede crear, el motivo."""
    from .pruebas import estado
    cat = catalogo()
    gcp = cat.get("gcp") or {}
    out = []
    for o in cat.get("opciones") or []:
        r = version_rama(o["rama"])
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, o["accion"])
        motivo = ""
        if not r.get("version"):
            otros = r.get("otros_canales") or []
            motivo = r.get("error") or "aún sin versión estable publicada"
            if otros:
                motivo += " · disponible solo en " + ", ".join(f"{x['canal']} ({x['version']})" for x in otros) + ": no se instala desde canales no estables"
        elif not a:
            motivo = f"falta la acción «{o['accion']}» en el catálogo (sincroniza con AAP)"
        elif o.get("plataforma") == "gcp" and not gcp.get("dominio"):
            motivo = "prerrequisito pendiente: zona DNS pública en Cloud DNS (dominio base) del proyecto " + gcp.get("proyecto", "")
        else:
            ep = estado(dict(a, cliente_id=cliente_id))["estado"]
            if ep != "certificada":
                motivo = "acción bloqueada: " + {"cambiada": "cambió desde su última prueba", "fallida": "su última prueba falló"}.get(ep, "su plan de prueba aún no está aprobado")
        out.append(dict(o, version=r.get("version", ""), canal=r.get("canal", ""), habilitada=not motivo, motivo=motivo,
                        auto_id=(a or {}).get("id")))
    return out


def parametros(opcion, f, gcp):
    nombre = (f.get("ocg_cluster") or "").strip().lower()
    if not re.fullmatch(r"[a-z]([a-z0-9-]{0,12}[a-z0-9])?", nombre):
        raise ValueError("Nombre del clúster: minúsculas, números y guiones, de 1 a 14 caracteres, empezando con letra")
    topo = f.get("ocg_topologia") or "sno"
    vence = int(f.get("ocg_vence_horas") or catalogo().get("vencimiento_defecto", 24))
    if topo not in ("sno", "completo") or not 1 <= vence <= 168:
        raise ValueError("Tipo SNO o completo; vencimiento entre 1 y 168 horas")
    return {"ocg_cluster": nombre, "ocg_version": opcion["version"], "ocg_topologia": topo, "ocg_vence_horas": vence,
            "ocg_horario": f.get("ocg_horario") or "siempre", "ocg_dominio": gcp.get("dominio", ""),
            "ocg_proyecto": gcp.get("proyecto", ""), "ocg_region": gcp.get("region", ""), "ocg_opcion": opcion["id"]}


@bp.route("/contenedores/crear/opciones")
@requiere_login
def contenedores_crear_opciones():
    _escritura()
    cat = catalogo()
    return jsonify({"opciones": opciones(g.cliente["id"]), "topologias": cat.get("topologias"), "gcp": cat.get("gcp"),
                    "vencimientos": cat.get("vencimiento_horas"), "vence_defecto": cat.get("vencimiento_defecto"),
                    "horarios": cat.get("horarios"), "horario_defecto": cat.get("horario_defecto")})


@bp.route("/contenedores/crear", methods=["POST"])
@requiere_login
def contenedores_crear():
    _escritura()
    from .aap import ErrorAAP
    from .operacion import solicitar
    o = next((x for x in opciones(g.cliente["id"]) if x["id"] == request.form.get("opcion")), None)
    if not o:
        abort(400)
    try:
        if not o["habilitada"]:
            raise ValueError(f"{o['nombre']}: {o['motivo']}")
        a = q1("SELECT * FROM automatizaciones WHERE id=?", o["auto_id"])
        p = parametros(o, request.form, catalogo().get("gcp") or {})
        t = solicitar(g.usuario, g.cliente, a, None, p, f"Crear plataforma de contenedores {o['nombre']} · {p['ocg_cluster']} "
                      f"(OpenShift {o['version']}, {p['ocg_topologia'].upper()})")
        flash(f"Ticket {t['numero']}: {o['nombre']} «{p['ocg_cluster']}» (OpenShift {o['version']}). Queda esperando aprobación; "
              "al terminar el clúster aparece en Contenedores", "ok")
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (ValueError, PermissionError, ErrorAAP) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.contenedores"))


@bp.route("/contenedores/<int:eid>/eliminar-gcp", methods=["POST"])
@requiere_login
def contenedores_eliminar_gcp(eid):
    """Ficha de un clúster creado en GCP: lanza «OpenShift · Eliminar en GCP» (ticket y aprobación)."""
    _escritura()
    import json
    from .aap import ErrorAAP
    from .operacion import OCP_GCP_ELIMINAR, solicitar
    e = q1("SELECT * FROM equipos WHERE id=? AND cliente_id=?", eid, g.cliente["id"])
    oc = (json.loads(e["datos"] or "{}").get("openshift") or {}) if e else {}
    if not e or oc.get("plataforma") != "gcp" or not oc.get("cluster"):
        abort(404)
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", g.cliente["id"], OCP_GCP_ELIMINAR)
    try:
        if not a:
            raise ValueError(f"Falta «{OCP_GCP_ELIMINAR}» en el catálogo (sincroniza con AAP)")
        gcp = catalogo().get("gcp") or {}
        t = solicitar(g.usuario, g.cliente, a, None, {"ocg_cluster": oc["cluster"], "ocg_modo": "eliminar", "ocg_proyecto": gcp.get("proyecto", ""),
                                                      "ocg_region": gcp.get("region", "")}, f"Eliminar el clúster {oc['cluster']} de GCP")
        flash(f"Ticket {t['numero']}: eliminar {oc['cluster']} en GCP (espera aprobación)", "ok")
        return redirect(url_for("consola.ticket", tid=t["id"]))
    except (ValueError, PermissionError, ErrorAAP) as x:
        flash(str(x), "error")
    return redirect(url_for("consola.equipo", eid=eid))
