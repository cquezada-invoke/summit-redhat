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
    items = [("job", t) for t in aap.plantillas()] + [("workflow", w) for w in aap.workflows()]
    for tipo, t in items:
        sf = t.get("summary_fields", {})
        org = (sf.get("organization") or {}).get("name", "General")
        if org in base.get("_ignorar_organizaciones", []) or t["name"] in base.get("_ignorar", []):
            continue
        version = ""
        pid = (sf.get("project") or {}).get("id")
        if pid:
            if pid not in proyectos:
                try:
                    pr = aap.proyecto(pid)
                    proyectos[pid] = (pr["name"], (pr.get("scm_revision") or "")[:8])
                except ErrorAAP:
                    proyectos[pid] = ("", "")
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
        existe = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND aap_tipo=? AND aap_id=?", cliente["id"], tipo, t["id"])
        campos_ficha = f.get("campos")  # campos definidos por FaceOps (variables sin encuesta en AAP)
        datos = {"nombre_aap": t["name"], "version": version, "proyecto": proyectos.get(pid, ("", ""))[0] if pid else "",
                 "inventario": inv, "campos": json.dumps(campos or campos_ficha or [], ensure_ascii=False), "actualizado": ahora()}
        if existe:
            # La ficha editada en FaceOps manda; desde AAP solo se refrescan nombre técnico, versión, inventario y formulario
            ej("UPDATE automatizaciones SET " + ", ".join(f"{k}=?" for k in datos) + " WHERE id=?", *datos.values(), existe["id"])
        else:
            datos.update({"nombre": nombre_simple, "descripcion": f.get("descripcion") or t.get("description") or "",
                          "categoria": f.get("categoria") or org, "tipo": tipo_rw, "ambito": f.get("ambito", inv),
                          "usa_limite": int(f.get("usa_limite", tipo == "job" and bool(inv))),
                          "requiere_aprobacion": int(f.get("requiere_aprobacion", 0)),
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


def segmentos_aplicables(usuario, cliente_id, auto):
    from .permisos import segmentos_visibles
    out = []
    for s in segmentos_visibles(usuario, cliente_id):
        lim = limite_para(cliente_id, auto, s["nombre"])
        if lim:
            out.append({"nombre": s["nombre"], "tipo": s["tipo"], "equipos": len(lim.split(":"))})
    return out


# ------------------------------------------------------------------ tickets
def numero_ticket(cliente):
    n = (q1("SELECT COUNT(*) n FROM tickets WHERE cliente_id=?", cliente["id"])["n"] or 0) + 1
    return f"FO-{cliente['codigo'].upper()}-{n:05d}"


def solicitar(usuario, cliente, auto, segmento, parametros, motivo):
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
    estado = "pendiente_aprobacion" if auto["requiere_aprobacion"] else "en_cola"
    tid = ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, limite, parametros, "
             "motivo, creado, valor_min, tarifa, aap_job_tipo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
             cliente["id"], numero_ticket(cliente), auto["id"], usuario["id"], estado, segmento or "", limite,
             json.dumps(parametros, ensure_ascii=False), motivo, ahora(), auto["minutos_manual"], auto["tarifa"], auto["aap_tipo"])
    t = q1("SELECT * FROM tickets WHERE id=?", tid)
    auditar(usuario["usuario"], cliente["id"], "solicitar", t["numero"],
            {"automatizacion": auto["nombre"], "version": auto["version"], "segmento": segmento, "limite": limite, "parametros": parametros})
    conector(cliente).crear(t, auto, usuario)
    if estado == "en_cola":
        ejecutar(t["id"])
    return q1("SELECT * FROM tickets WHERE id=?", tid)


def ejecutar(ticket_id):
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
    try:
        params = j(t["parametros"], {})
        params.update({"faceops_ticket": t["numero"], "faceops_solicitante": (q1("SELECT usuario FROM usuarios WHERE id=?", t["usuario_id"]) or {}).get("usuario", "")})
        job = AAP.de_cliente(c).lanzar(a["aap_tipo"], a["aap_id"], extra_vars=params, limite=t["limite"] or None)
        ej("UPDATE tickets SET estado='en_ejecucion', aap_job_id=?, inicio=? WHERE id=?", job, ahora(), ticket_id)
    except ErrorAAP as e:
        ej("UPDATE tickets SET estado='fallido', resultado=?, fin=? WHERE id=?", str(e)[:1000], ahora(), ticket_id)
        conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", ticket_id), a)


def resolver_aprobacion(usuario, ticket_id, aprobar, comentario=""):
    t = q1("SELECT * FROM tickets WHERE id=?", ticket_id)
    if t["estado"] != "pendiente_aprobacion":
        return
    ej("UPDATE tickets SET aprobado_por=?, respuesta=? WHERE id=?", usuario["id"], comentario, ticket_id)
    auditar(usuario["usuario"], t["cliente_id"], "aprobar" if aprobar else "rechazar", t["numero"], {"comentario": comentario})
    if aprobar:
        ej("UPDATE tickets SET estado='en_cola' WHERE id=?", ticket_id)
        ejecutar(ticket_id)
    else:
        ej("UPDATE tickets SET estado='rechazado', fin=?, resultado=? WHERE id=?", ahora(), f"Rechazado: {comentario}", ticket_id)
        c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
        conector(c).cerrar(q1("SELECT * FROM tickets WHERE id=?", ticket_id), q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"]))


ESTADOS_AAP = {"successful": "exitoso", "failed": "fallido", "error": "fallido", "canceled": "cancelado"}


def actualizar_tickets():
    for t in q("SELECT * FROM tickets WHERE estado='en_ejecucion' AND aap_job_id IS NOT NULL"):
        c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
        try:
            aap = AAP.de_cliente(c)
            r = aap.trabajo(t["aap_job_tipo"], t["aap_job_id"])
        except ErrorAAP:
            continue
        if r.get("status") in ESTADOS_AAP:
            est = ESTADOS_AAP[r["status"]]
            dur = int(r.get("elapsed") or 0)
            res = f"AAP terminó en {dur} s ({r['status']})"
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


class ServiceNow(Conector):
    """Espeja cada ticket de FaceOps como incidente en el ServiceNow del cliente (se abre al pedir y se resuelve al terminar)."""

    def _api(self, metodo, ruta, datos=None):
        r = requests.request(metodo, self.conf["url"].rstrip("/") + ruta, json=datos, timeout=30,
                             auth=(self.conf["usuario"], self.conf["clave"]), headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json().get("result", {})

    def crear(self, t, auto, usuario):
        try:
            r = self._api("POST", "/api/now/table/incident", {
                "short_description": f"[FaceOps {t['numero']}] {auto['nombre']}" + (f" · {t['segmento']}" if t["segmento"] else ""),
                "description": f"Solicitado por {usuario['nombre']} en FaceOps.\nMotivo: {t['motivo'] or '—'}\n"
                               f"Automatización: {auto['nombre_aap']} ({auto['version']})\nÁmbito: {t['limite'] or auto['ambito']}",
                "correlation_id": t["numero"], "impact": "3", "urgency": "3", "category": "software"})
            ej("UPDATE tickets SET externo_ref=? WHERE id=?", f"{r.get('number')}|{r.get('sys_id')}", t["id"])
        except (requests.RequestException, KeyError) as e:
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200]})

    def cerrar(self, t, auto):
        if not t.get("externo_ref") or "|" not in t["externo_ref"]:
            return
        sys_id = t["externo_ref"].split("|")[1]
        ok = t["estado"] == "exitoso"
        try:
            self._api("PATCH", f"/api/now/table/incident/{sys_id}", {
                "state": "6", "close_code": "Solved (Permanently)" if ok else "Solved (Work Around)",
                "close_notes": f"FaceOps {t['numero']}: {t['estado']}. {(t['resultado'] or '')[:500]}"})
        except requests.RequestException as e:
            auditar("sistema", t["cliente_id"], "error conector tickets", t["numero"], {"error": str(e)[:200]})


def conector(cliente):
    return {"servicenow": ServiceNow}.get(cliente.get("tickets_tipo"), Conector)(cliente)
