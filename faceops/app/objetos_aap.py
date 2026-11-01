"""Objetos de configuración de Ansible Automation Platform que usa cada automatización (organización, proyecto y
repositorio, plantilla, inventario, credenciales, entorno de ejecución, grupos de instancias, encuesta y el workflow de
aprobación que crea FaceOps), con su enlace en AAP. Cache de 5 minutos por automatización."""
import time

_CACHE = {}
TTL = 300

RUTAS = {"organización": "access/organizations/{id}/details", "proyecto": "execution/projects/{id}/details",
         "inventario": "execution/infrastructure/inventories/inventory/{id}/details",
         "credencial": "execution/infrastructure/credentials/{id}/details",
         "entorno de ejecución": "execution/infrastructure/execution-environments/{id}/details",
         "grupo de instancias": "execution/infrastructure/instance-groups/{id}/details",
         "plantilla": "execution/templates/job-template/{id}/details", "workflow": "execution/templates/workflow-job-template/{id}/details"}


def _url(cli, tipo, id_):
    return f"{cli.publica}/{RUTAS[tipo].format(id=id_)}" if id_ and tipo in RUTAS else None


def _de_plantilla(cli, tipo, id_, via=""):
    """Objetos de una plantilla o workflow de AAP."""
    out = []
    ruta = "job_templates" if tipo == "job" else "workflow_job_templates"
    t = cli.get(f"{ruta}/{id_}/")
    sf = t.get("summary_fields") or {}
    nombre_tipo = "plantilla" if tipo == "job" else "workflow"
    out.append({"tipo": nombre_tipo, "nombre": t.get("name"), "id": id_, "detalle": t.get("playbook") or "", "via": via})
    if sf.get("organization"):
        out.append({"tipo": "organización", "nombre": sf["organization"]["name"], "id": sf["organization"]["id"], "via": via})
    if sf.get("project"):
        p = cli.get(f"projects/{sf['project']['id']}/")
        out.append({"tipo": "proyecto", "nombre": p.get("name"), "id": p.get("id"),
                    "detalle": f"{p.get('scm_url', '')} · rama {p.get('scm_branch') or 'por defecto'}", "via": via})
    if sf.get("inventory"):
        out.append({"tipo": "inventario", "nombre": sf["inventory"]["name"], "id": sf["inventory"]["id"],
                    "detalle": "se elige al lanzar" if t.get("ask_inventory_on_launch") else "", "via": via})
    if sf.get("execution_environment"):
        ee = sf["execution_environment"]
        out.append({"tipo": "entorno de ejecución", "nombre": ee.get("name"), "id": ee.get("id"), "detalle": ee.get("image", ""), "via": via})
    for c in sf.get("credentials") or []:
        out.append({"tipo": "credencial", "nombre": c.get("name"), "id": c.get("id"), "detalle": c.get("kind", ""), "via": via})
    if tipo == "job":
        for ig in (cli.get(f"{ruta}/{id_}/instance_groups/").get("results") or []):
            out.append({"tipo": "grupo de instancias", "nombre": ig.get("name"), "id": ig.get("id"), "via": via})
    if t.get("survey_enabled"):
        out.append({"tipo": "encuesta", "nombre": "preguntas al lanzar", "id": None, "detalle": f"en {nombre_tipo} #{id_}", "via": via})
    return out


def de(cliente, a):
    """Lista de objetos de AAP de la automatización (sin repetidos), con su enlace."""
    k = (cliente["id"], a["id"])
    if k in _CACHE and time.time() - _CACHE[k][0] < TTL:
        return _CACHE[k][1]
    from .aap import AAP
    from .bd import j, q1
    try:
        cli = AAP.de_cliente(cliente)
        objs = []
        tec = j(a.get("tecnico"), {})
        if a.get("aap_id") and a["aap_tipo"] in ("job", "workflow"):
            objs += _de_plantilla(cli, a["aap_tipo"], a["aap_id"])
            if a["aap_tipo"] == "workflow":
                for n in {x.get("ujt_id") for x in tec.get("nodos") or [] if x.get("tipo") == "plantilla" and x.get("ujt_id")}:
                    objs += _de_plantilla(cli, "job", n, via="paso del workflow")
        for v in (tec.get("compuesta") or {}).values():   # automatización compuesta de FaceOps: las que elige según la acción
            b = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], v.get("automatizacion"))
            if b and b["aap_id"]:
                objs += _de_plantilla(cli, b["aap_tipo"], b["aap_id"], via=f"según la acción · {b['nombre_aap']}")
        # el workflow de aprobación por áreas que FaceOps crea delante de la plantilla
        for w in cli.get("workflow_job_templates/", name__startswith=f"FaceOps · Aprobar › {a['nombre_aap']}").get("results") or []:
            objs.append({"tipo": "workflow", "nombre": w["name"], "id": w["id"], "detalle": "aprobación por áreas (lo crea FaceOps)", "via": "aprobación"})
        vistos, out = set(), []
        for o in objs:
            c = (o["tipo"], o.get("id") or o["nombre"])
            if c in vistos:
                continue
            vistos.add(c)
            o["url"] = _url(cli, o["tipo"], o.get("id"))
            out.append(o)
        orden = list(RUTAS) + ["encuesta"]
        out.sort(key=lambda o: (orden.index(o["tipo"]) if o["tipo"] in orden else 99))
    except Exception as x:  # noqa: BLE001 - sin AAP la ficha igual se muestra
        out = [{"tipo": "error", "nombre": f"No se pudo leer AAP: {str(x)[:120]}", "id": None, "url": None}]
    _CACHE[k] = (time.time(), out)
    return out
