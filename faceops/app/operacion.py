"""Catálogo de automatizaciones (desde AAP + ficha común de FaceOps), ejecución y tickets."""
import json
import os
import re
import threading
import time
from datetime import datetime

import requests
import yaml

from .aap import AAP, ErrorAAP
from .bd import ahora, auditar, ej, j, q, q1
from .inventario import normal
from .permisos import equipos_de_segmento, puede_ejecutar, puede_ver_segmento

AQUI = os.path.dirname(os.path.abspath(__file__))
FICHAS = os.path.join(AQUI, "..", "datos", "catalogo.yml")
PALABRAS_LECTURA = ("verificar", "estado", "reporte", "recolectar", "consultar", "listar", "auditar", "revisar", "prueba de", "probar")


GUIAS = os.path.join(AQUI, "..", "datos", "guias.yml")
GUIAS_DEMOS = os.path.join(AQUI, "..", "datos", "guias-demos.yml")
DEMOS = os.path.join(AQUI, "..", "datos", "demos.yml")
SNOW = os.path.join(AQUI, "..", "datos", "servicenow.yml")
FORMULARIOS = os.path.join(AQUI, "..", "datos", "formularios.yml")
TIPOS_AMIGABLES = ("equipo", "equipos", "fecha", "ip")


def combinar_campos(encuesta, formulario):
    """La encuesta de AAP manda (es lo que AAP valida); el formulario de FaceOps la hace amigable.
    Sin encuesta, el formulario define los campos."""
    if not encuesta:
        return formulario
    por_var = {c["var"]: c for c in formulario}
    out = []
    for c in encuesta:
        f = por_var.get(c["var"], {})
        n = dict(c)
        for k in ("etiqueta", "ayuda"):
            if f.get(k):
                n[k] = f[k]
        if f.get("tipo") in TIPOS_AMIGABLES and c["tipo"] in ("text", "multiplechoice", "multiselect"):
            n["tipo"] = f["tipo"]
        out.append(n)
    return out


def _yaml(ruta, defecto):
    try:
        return yaml.safe_load(open(ruta)) or defecto
    except FileNotFoundError:
        return defecto


def orden_demos():
    """nombre técnico → (demo, orden) según datos/demos.yml; lo demás queda en «Otras automatizaciones»."""
    out = {}
    for i, d in enumerate(_yaml(DEMOS, [])):
        for k, n in enumerate(d.get("automatizaciones", [])):
            out[n] = (d["demo"], i * 100 + k)
    return out


def resumen_demos():
    return {d["demo"]: d.get("resumen", "") for d in _yaml(DEMOS, [])}


def conf_servicenow(nombre_aap):
    return _yaml(SNOW, {}).get(nombre_aap, {})


def fichas_base():
    try:
        return yaml.safe_load(open(FICHAS)) or {}
    except FileNotFoundError:
        return {}


# ------------------------------------------------------------------ catálogo
def sincronizar_catalogo(cliente, usuario="sistema"):
    aap = AAP.de_cliente(cliente)
    base = fichas_base()
    defaults_org = base.get("_por_organizacion", {})
    fichas = base.get("fichas", {})
    proyectos, n = {}, 0
    guias, demos, formularios = {**_yaml(GUIAS, {}), **_yaml(GUIAS_DEMOS, {})}, orden_demos(), _yaml(FORMULARIOS, {})
    items = [("job", t) for t in aap.plantillas()] + [("workflow", w) for w in aap.workflows()]
    for tipo, t in items:
        sf = t.get("summary_fields", {})
        org = (sf.get("organization") or {}).get("name", "General")
        if org in base.get("_ignorar_organizaciones", []) or t["name"] in base.get("_ignorar", []) \
                or t["name"].startswith("FaceOps · Aprobar ›"):
            continue
        version = ""
        pid = (sf.get("project") or {}).get("id")
        if pid:
            if pid not in proyectos:
                try:
                    pr = aap.proyecto(pid)
                    proyectos[pid] = (pr["name"], (pr.get("scm_revision") or "")[:8], pr.get("scm_url", ""), pr.get("scm_branch", ""))
                except ErrorAAP:
                    proyectos[pid] = ("", "", "", "")
            version = f"{proyectos[pid][0]} @ {proyectos[pid][1]}" if proyectos[pid][1] else proyectos[pid][0]
        else:
            version = "workflow · " + (t.get("modified") or "")[:10]
        campos = []
        for c in aap.encuesta(tipo, t["id"]) if t.get("survey_enabled") else []:
            campos.append({"var": c["variable"], "etiqueta": c.get("question_name", c["variable"]), "tipo": c.get("type", "text"),
                           "requerido": bool(c.get("required")), "defecto": c.get("default", ""),
                           "opciones": c.get("choices") if isinstance(c.get("choices"), list) else
                           [x for x in str(c.get("choices") or "").split("\n") if x], "ayuda": c.get("question_description", "")})
        f = dict(defaults_org.get(org, {}))
        f.update(fichas.get(t["name"], {}))
        nombre_simple = f.get("nombre") or re.sub(r"^[^·]+·\s*", "", t["name"])
        tipo_rw = f.get("tipo") or ("lectura" if any(p in normal(t["name"]) for p in PALABRAS_LECTURA) else "escritura")
        inv = (sf.get("inventory") or {}).get("name", "")
        if tipo == "job":
            pr = proyectos.get(pid, ("", "", "", ""))
            tecnico = {"playbook": t.get("playbook", ""), "proyecto": pr[0], "repositorio": pr[2], "rama": pr[3] or "por defecto",
                       "commit": pr[1], "inventario": inv, "organizacion": org,
                       "credenciales": [c["name"] for c in sf.get("credentials", [])], "descripcion_aap": t.get("description", "")}
            aprob_aap = ""
        else:
            try:
                nodos = aap.nodos_workflow(t["id"])
            except ErrorAAP:
                nodos = []
            tecnico = {"nodos": nodos, "organizacion": org, "inventario": inv, "descripcion_aap": t.get("description", "")}
            aprob_aap = " · ".join(x["nombre"] for x in nodos if x["tipo"] == "aprobacion")
        demo, orden = demos.get(t["name"], ("", 999))
        existe = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND aap_tipo=? AND aap_id=?", cliente["id"], tipo, t["id"])
        form = formularios.get(t["name"], {})
        campos_ficha = combinar_campos(campos, form.get("campos") or f.get("campos") or [])
        datos = {"nombre_aap": t["name"], "version": version, "proyecto": proyectos.get(pid, ("", ""))[0] if pid else "",
                 "inventario": inv, "campos": json.dumps(campos_ficha or [], ensure_ascii=False), "actualizado": ahora(),
                 "tecnico": json.dumps(tecnico, ensure_ascii=False), "demo": demo, "orden": orden, "aprobacion_aap": aprob_aap}
        if "usa_limite" in form:
            datos["usa_limite"] = int(bool(form["usa_limite"]))
        if aprob_aap:
            datos["requiere_aprobacion"] = 1  # la aprobación ya vive en el workflow de AAP
        # La guía viene de datos/guias*.yml mientras nadie la edite en FaceOps-admin (al editarla pierde la marca _origen)
        g_yml = dict(guias.get(t["name"], {}))
        g_bd = j(existe.get("guia"), {}) if existe else {}
        if g_yml and (not g_bd or "_origen" in g_bd):
            g_yml["_origen"] = "datos"
            datos["guia"] = json.dumps(g_yml, ensure_ascii=False)
            if g_yml.get("nombre"):
                datos["nombre"] = g_yml["nombre"]
        if existe:
            # La ficha editada en FaceOps manda; desde AAP solo se refrescan nombre técnico, versión, inventario y formulario
            ej("UPDATE automatizaciones SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=?", *datos.values(), existe["id"])
        else:
            datos.update({"nombre": datos.get("nombre") or nombre_simple, "descripcion": f.get("descripcion") or t.get("description") or "",
                          "categoria": f.get("categoria") or org, "tipo": tipo_rw, "ambito": f.get("ambito", inv),
                          "usa_limite": int(datos.get("usa_limite", f.get("usa_limite", tipo == "job" and bool(inv)))),
                          "requiere_aprobacion": int(datos.get("requiere_aprobacion", f.get("requiere_aprobacion", 0))),
                          "minutos_manual": int(f.get("minutos_manual", 30)), "tarifa": float(f.get("tarifa", 0)),
                          "etiquetas": json.dumps(f.get("etiquetas", [])), "info": f.get("info", ""),
                          "visible": int(f.get("visible", 1))})
            ej("INSERT INTO automatizaciones(cliente_id, aap_tipo, aap_id, " + ", ".join(datos) + ") VALUES (?,?,?," +
               ",".join("?" * len(datos)) + ")", cliente["id"], tipo, t["id"], *datos.values())
        n += 1
    auditar(usuario, cliente["id"], "sincronizar catálogo", "catálogo", {"automatizaciones": n})
    return n


def limite_para(cliente_id, auto, segmento):
    """Hosts del segmento que existen en el inventario de la automatización (patrón de --limit de AAP)."""
    if not auto["usa_limite"] or not segmento:
        return ""
    eqs = [e for e in equipos_de_segmento(cliente_id, segmento)
           if e["estado"] != "retirado" and (not auto["inventario"] or auto["inventario"] in j(e["inventarios"], []))]
    return ":".join(sorted({e["nombre"] for e in eqs}))


def equipos_elegibles(usuario, cliente_id, auto):
    """Equipos que el usuario puede elegir en un campo «equipo/equipos»: los de sus segmentos que están en el
    inventario de la automatización (o todos sus equipos si la automatización no tiene inventario fijo)."""
    from .permisos import segmentos_visibles
    nombres = {}
    for s in segmentos_visibles(usuario, cliente_id):
        for e in equipos_de_segmento(cliente_id, s["nombre"]):
            if e["estado"] != "retirado" and (not auto["inventario"] or auto["inventario"] in j(e["inventarios"], [])):
                nombres.setdefault(e["nombre"], e.get("ambiente") or s["nombre"])
    return [{"nombre": n, "detalle": d} for n, d in sorted(nombres.items())]


def segmentos_aplicables(usuario, cliente_id, auto):
    from .permisos import segmentos_visibles
    out = []
    for s in segmentos_visibles(usuario, cliente_id):
        lim = limite_para(cliente_id, auto, s["nombre"])
        if lim:
            out.append({"nombre": s["nombre"], "tipo": s["tipo"], "equipos": len(lim.split(":")), "limite": lim})
    return out


# ------------------------------------------------------------------ tickets
def numero_ticket(cliente):
    n = (q1("SELECT COUNT(*) n FROM tickets WHERE cliente_id=?", cliente["id"])["n"] or 0) + 1
    return f"FO-{cliente['codigo'].upper()}-{n:05d}"


def solicitar(usuario, cliente, auto, segmento, parametros, motivo, secretos=None):
    """FaceOps orquesta: crea el ticket (propio y, si corresponde, en la herramienta del cliente) y lanza en AAP.
    Si requiere aprobación, la aprobación vive en AAP (nodo de aprobación del workflow): FaceOps la muestra y
    permite responderla, y quien entre a AAP la ve igual en su bandeja de aprobaciones."""
    ok, por_que = puede_ejecutar(usuario, cliente["id"], auto)
    if not ok:
        raise PermissionError(por_que)
    limite = ""
    if auto["usa_limite"]:
        if not segmento or not puede_ver_segmento(usuario, cliente["id"], segmento):
            raise PermissionError("Elige un segmento al que tengas acceso")
        limite = limite_para(cliente["id"], auto, segmento)
        if not limite:
            raise PermissionError("El segmento no tiene equipos en el inventario de esta automatización")
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, "
             "motivo, creado, valor_min, tarifa, aap_job_tipo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], numero_ticket(cliente), auto["id"], usuario["id"], "en_cola", segmento or "", limite,
             json.dumps(parametros, ensure_ascii=False), motivo, ahora(), auto["minutos_manual"], auto["tarifa"], auto["aap_tipo"])
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    auditar(usuario["usuario"], cliente["id"], "solicitar", t["numero"],
            {"automatizacion": auto["nombre"], "version": auto["version"], "segmento": segmento, "limite": limite, "parametros": parametros})
    extra = conector(cliente).crear(t, auto, usuario) or {}
    if extra:
        ej("UPDATE tickets SET parametros=? WHERE id=?", json.dumps(dict(parametros, **extra), ensure_ascii=False), tid)
    ejecutar(tid, secretos)  # las claves van solo a AAP: en el ticket y la auditoría quedan enmascaradas
    return q1("SELECT * FROM tickets WHERE id=?", tid)


def objetivo_aap(aap, auto):
    """Qué se lanza en AAP: la plantilla tal cual, o (si FaceOps exige aprobación y AAP no la tiene) su workflow de aprobación."""
    if auto["requiere_aprobacion"] and not auto.get("aprobacion_aap"):
        wf = auto.get("aprobacion_wf")
        if not wf:
            wf = aap.asegurar_flujo_aprobacion(auto)
            ej("UPDATE automatizaciones SET aprobacion_wf=? WHERE id=?", wf, auto["id"])
        return "workflow", wf
    return auto["aap_tipo"], auto["aap_id"]


def ejecutar(ticket_id, secretos=None):
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    try:
        aap = AAP.de_cliente(c)
        params = j(t["parametros"], {})
        for k, v in conf_servicenow(a["nombre_aap"]).get("extra", {}).items():  # variables fijas (p. ej. solicitud_tipo)
            if "{" not in str(v):
                params.setdefault(k, v)
        params.update({"faceops_ticket": t["numero"], "faceops_solicitante": (q1("SELECT usuario FROM usuarios WHERE id=?", t["usuario_id"]) or {}).get("usuario", "")})
        params.setdefault("solicitado_por", params["faceops_solicitante"])
        params.update(secretos or {})
        tipo, id_ = objetivo_aap(aap, a)
        job = aap.lanzar(tipo, id_, extra_vars=params, limite=t["limite"] or None)
        ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, aap_job_tipo=?, inicio=? WHERE id=?", job, tipo, ahora(), ticket_id)
    except ErrorAAP as e:
        ej("UPDATE tickets SET estado='fallido', resultado=?, fin=? WHERE id=?", str(e)[:1000], ahora(), ticket_id)
        conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", ticket_id), a)


def resolver_aprobacion(usuario, ticket_id, aprobar, comentario=""):
    """Responde en AAP la aprobación pendiente (queda registrada en AAP) y deja la traza en FaceOps."""
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    if t["estado"] != "pendiente_aprobacion" or not t["aap_aprobacion_id"]:
        return
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    AAP.de_cliente(c).responder_aprobacion(t["aap_aprobacion_id"], aprobar)
    ej("UPDATE tickets SET aprobado_por=?, respuesta=?, aprobado_en=?, estado=? WHERE id=?", usuario["id"], comentario,
       f"FaceOps ({usuario['usuario']}) → AAP", "en_ejecucion" if aprobar else "en_ejecucion", ticket_id)
    auditar(usuario["usuario"], t["cliente_id"], "aprobar en AAP" if aprobar else "rechazar en AAP", t["numero"],
            {"comentario": comentario, "aprobacion_aap": t["aap_aprobacion_id"]})


ESTADOS_AAP = {"successful": "exitoso", "failed": "fallido", "error": "fallido", "canceled": "cancelado"}


def _revisar_aprobaciones(aap, t):
    """Lee en AAP los nodos de aprobación del workflow del ticket. Devuelve 'rechazado' | 'vencido' | None."""
    final = None
    for job in aap.nodos_trabajo_workflow(t["aap_job_id"]):
        if job.get("type") != "workflow_approval" or not job.get("id"):
            continue
        if job.get("status") == "pending":
            if t["estado"] != "pendiente_aprobacion" or t["aap_aprobacion_id"] != job["id"]:
                ej("UPDATE tickets SET estado='pendiente_aprobacion', aap_aprobacion_id=? WHERE id=?", job["id"], t["id"])
                auditar("AAP", t["cliente_id"], "espera aprobación en AAP", t["numero"], {"aprobacion": job.get("name"), "id": job["id"]})
            return None
        if t["aap_aprobacion_id"] == job["id"] or job.get("status") in ("successful", "failed"):
            try:
                ap = aap.aprobacion(job["id"])
            except ErrorAAP:
                continue
            quien = ((ap.get("summary_fields") or {}).get("approved_or_denied_by") or {}).get("username", "")
            if ap.get("timed_out"):
                final = "vencido"
            elif job.get("status") == "failed":
                final = "rechazado"
            if t["estado"] == "pendiente_aprobacion" and t["aap_aprobacion_id"] == job["id"]:
                txt = t["aprobado_en"] or ""
                if not txt.startswith("FaceOps"):
                    txt = f"AAP ({quien or 'vencida'})"
                ej("UPDATE tickets SET estado='en_ejecucion', aprobado_en=? WHERE id=?", txt, t["id"])
                auditar("AAP", t["cliente_id"], {"successful": "aprobado en AAP", "failed": "rechazado en AAP"}.get(job.get("status"), "aprobación"),
                        t["numero"], {"por": quien, "vencida": bool(ap.get("timed_out"))})
                t = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    return final


def actualizar_tickets():
    for t in q("SELECT * FROM tickets WHERE estado IN ('en_ejecucion','pendiente_aprobacion') AND aap_job_id IS NOT NULL"):
        c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
        try:
            aap = AAP.de_cliente(c)
            negado = _revisar_aprobaciones(aap, t) if t["aap_job_tipo"] == "workflow" else None
            r = aap.trabajo(t["aap_job_tipo"], t["aap_job_id"])
        except ErrorAAP:
            continue
        if r.get("status") in ESTADOS_AAP:
            est = negado or ESTADOS_AAP[r["status"]]
            dur = int(r.get("elapsed") or 0)
            res = f"AAP terminó en {dur} s ({r['status']})" + {"rechazado": " · la aprobación fue rechazada",
                                                                 "vencido": " · la aprobación venció sin respuesta"}.get(negado or "", "")
            if est == "fallido" and t["aap_job_tipo"] == "job":
                res += "\n" + aap.salida(t["aap_job_id"], 15)
            ej("UPDATE tickets SET estado=?, fin=?, duracion_s=?, resultado=? WHERE id=?", est, ahora(), dur, res, t["id"])
            conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", t["id"]), q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]))


def vigia(intervalo=10):
    """Hilo de fondo: estado de los trabajos en AAP y recolección diaria del inventario (03:00 hora del servidor)."""
    from .inventario import recolectar, sincronizar

    def bucle():
        ultimo_dia = None
        while True:
            try:
                actualizar_tickets()
                hoy = datetime.now()
                if hoy.hour == 3 and ultimo_dia != hoy.date():
                    ultimo_dia = hoy.date()
                    for c in q("SELECT * FROM clientes WHERE activo=1 AND aap_url!=''"):
                        try:
                            recolectar(c)
                            time.sleep(600)
                            sincronizar(c)
                            sincronizar_catalogo(c)
                        except Exception as e:  # noqa: BLE001
                            auditar("sistema", c["id"], "error recolección", "inventario", {"error": str(e)[:300]})
            except Exception:  # noqa: BLE001
                pass
            time.sleep(intervalo)
    threading.Thread(target=bucle, daemon=True, name="faceops-vigia").start()


# ------------------------------------------------------------------ conectores de tickets
class Conector:
    def __init__(self, cliente):
        self.c = cliente
        self.conf = j(cliente.get("tickets_conf"), {})

    def crear(self, t, auto, usuario):
        pass

    def cerrar(self, t, auto):
        pass

    def enlace(self, ref):
        return None


class ServiceNow(Conector):
    """ServiceNow orquestado desde FaceOps (modo por automatización en datos/servicenow.yml):
      · ritm   → pide el ítem del catálogo de ServiceNow de la demo, toma el ticket (RITM) y se lo pasa a AAP;
                 los playbooks lo actualizan y lo cierran como siempre (espera de aprobación, completado, rechazado).
      · propio → la automatización abre su propio incidente/cambio; FaceOps lo enlaza al terminar.
      · por defecto → FaceOps abre un incidente al pedir y lo resuelve al terminar."""

    def _api(self, metodo, ruta, datos=None, **params):
        r = requests.request(metodo, self.conf["url"].rstrip("/") + ruta, json=datos, params=params or None, timeout=30,
                             auth=(self.conf["usuario"], self.conf["clave"]), headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json().get("result", {})

    def enlace(self, ref):
        p = (ref or "").split("|")
        if len(p) < 3 or not self.conf.get("url"):
            return None
        return f"{self.conf['url'].rstrip('/')}/nav_to.do?uri={p[2]}.do?sys_id={p[1]}"

    def crear(self, t, auto, usuario):
        sn = conf_servicenow(auto["nombre_aap"])
        modo = sn.get("modo", "incidente")
        try:
            if modo == "propio":
                ej("UPDATE tickets SET externo_ref=? WHERE id=?", "propio", t["id"])
                return {}
            if modo == "ritm":
                return self._pedir_item(t, auto, usuario, sn)
            r = self._api("POST", "/api/now/table/incident", {
                "short_description": f"[FaceOps {t['numero']}] {auto['nombre']}" + (f" · {t['segmento']}" if t["segmento"] else ""),
                "description": f"Solicitado por {usuario['nombre']} en FaceOps.\nMotivo: {t['motivo'] or '—'}\n"
                               f"Automatización: {auto['nombre_aap']} ({auto['version']})\nÁmbito: {t['limite'] or auto['ambito']}",
                "correlation_id": f"faceops:{t['numero']}", "impact": "3", "urgency": "3", "category": "software"})
            ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{r.get('number')}|{r.get('sys_id')}|incident", t["id"])
        except (requests.RequestException, KeyError, IndexError) as e:
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200]})
        return {}

    def _pedir_item(self, t, auto, usuario, sn):
        params = j(t["parametros"], {})
        item = self._api("GET", "/api/now/table/sc_cat_item", sysparm_query=f"name={sn['item']}", sysparm_fields="sys_id", sysparm_limit=1)[0]
        variables = {k: str(params[v]) for k, v in sn.get("variables", {}).items() if params.get(v) not in (None, "")}
        variables["motivo"] = t["motivo"] or f"Solicitado en FaceOps ({t['numero']})"
        orden = self._api("POST", f"/api/sn_sc/servicecatalog/items/{item['sys_id']}/order_now",
                          {"sysparm_quantity": "1", "variables": variables})
        ritm = self._api("GET", "/api/now/table/sc_req_item", sysparm_query=f"request={orden['request_id']}",
                         sysparm_fields="sys_id,number,sys_created_on", sysparm_limit=1)[0]
        # FaceOps lo toma en el acto (el vigía de la demo deja pasar 25 s): así se lanza una sola vez, desde FaceOps
        self._api("PATCH", f"/api/now/table/sc_req_item/{ritm['sys_id']}", {
            "state": "2", "correlation_id": f"faceops:{t['numero']}",
            "comments": f"Recibimos tu solicitud desde FaceOps ({t['numero']}). Te avisaremos aquí mismo.",
            "work_notes": f"Orquestado por FaceOps {t['numero']} · solicitado por {usuario['nombre']} ({usuario['usuario']}). "
                          f"FaceOps lanza en AAP «{auto['nombre_aap']}»."})
        ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{ritm['number']}|{ritm['sys_id']}|sc_req_item", t["id"])
        extra = {"snow_ritm": ritm["number"], "snow_ritm_sys_id": ritm["sys_id"], "snow_creado": ritm.get("sys_created_on", ""),
                 "snow_motivo": variables["motivo"]}
        for k, v in sn.get("extra", {}).items():
            if "{ritm}" in str(v) and not params.get(k):
                extra[k] = str(v).replace("{ritm}", ritm["number"])
        auditar("FaceOps", t["cliente_id"], "ticket en ServiceNow", t["numero"], {"ritm": ritm["number"], "item": sn["item"]})
        return extra

    def cerrar(self, t, auto):
        ref = t.get("externo_ref") or ""
        try:
            if ref == "propio":
                b = conf_servicenow(auto["nombre_aap"]).get("buscar")
                if b and t["aap_job_id"]:
                    r = self._api("GET", f"/api/now/table/{b['tabla']}", sysparm_query=b["consulta"].replace("{job}", str(t["aap_job_id"])),
                                  sysparm_fields="sys_id,number", sysparm_limit=1)
                    if r:
                        ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{r[0]['number']}|{r[0]['sys_id']}|{b['tabla']}", t["id"])
                return
            p = ref.split("|")
            if len(p) < 3:
                return
            if p[2] == "sc_req_item":
                # El playbook ya cerró o actualizó el RITM; FaceOps solo deja constancia
                self._api("PATCH", f"/api/now/table/sc_req_item/{p[1]}", {"work_notes": f"FaceOps {t['numero']}: {t['estado']}."})
                return
            notas = f"FaceOps {t['numero']}: {t['estado']}. {(t['resultado'] or '')[:500]}"
            if t["estado"] == "fallido":  # queda abierto para que lo tome una persona
                self._api("PATCH", f"/api/now/table/incident/{p[1]}", {"state": "2", "work_notes": "Ansible no pudo resolverlo: " + notas})
            else:
                self._api("PATCH", f"/api/now/table/incident/{p[1]}", {
                    "state": "6", "close_notes": notas, "comments": notas,
                    "close_code": "Resolved – Permanent Fix Applied" if t["estado"] == "exitoso" else "Resolved – Fixed Without Action"})
        except (requests.RequestException, KeyError) as e:
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200]})


def conector(cliente):
    return {"servicenow": ServiceNow}.get(cliente.get("tickets_tipo"), Conector)(cliente)
