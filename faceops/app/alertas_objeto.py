"""Alertas por objeto: cada objeto del sistema (servidor, storage, equipo de red…) tiene su botón «Alertas».

El catálogo y la configuración viven en Ansible: FaceOps ofrece las alertas que aplican al tipo de objeto (catálogo
aap/files/alertas-objetos.yml, el mismo que usa el playbook) y pide su configuración con un ticket a
«Monitoreo · Configurar alertas del equipo», que la escribe en Prometheus. FaceOps guarda solo el reflejo de lo que Ansible
dejó aplicado (datos.alertas del equipo, desde los artefactos del ticket) para mostrar el estado del botón."""
import json
import os

import yaml

from .bd import ahora, ej, j, q, q1

AUTO = "Monitoreo · Configurar alertas del equipo"
RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aap", "files", "alertas-objetos.yml")
RED = ("router", "switch", "firewall", "balanceador")
_cache = {}


def catalogo():
    m = os.path.getmtime(RUTA)
    if _cache.get("m") != m:
        _cache.update(m=m, d=yaml.safe_load(open(RUTA, encoding="utf-8")))
    return _cache["d"]


def tipo_de(e):
    """linux | windows | storage | red | None (sin alertas para este objeto todavía)."""
    c = (e.get("categoria") or "").lower()
    so = (e.get("so") or "").lower()
    if c in RED:
        return "red"
    if c == "almacenamiento":
        return "storage"
    if c.startswith("clúster") or c in ("kubernetes", "openshift"):
        return None
    if "windows" in so:
        return "windows"
    so = f"{so} {(e.get('so_familia') or '').lower()}"
    if any(x in so for x in ("linux", "red hat", "redhat", "rhel", "rocky", "alma", "centos", "ubuntu", "debian", "oracle", "suse", "fedora")):
        return "linux"
    if c in ("", "servidor", "estación de trabajo") and not so.strip():
        return "linux"   # aún sin inventariar: se ofrece como Linux; el playbook verifica el agente antes de aplicar
    return None


def aplicadas(e):
    """Lo que Ansible dejó configurado para el equipo (reflejo del último ticket exitoso)."""
    return (j(e.get("datos"), {}) or {}).get("alertas") or {}


def en_curso(cliente_id, e):
    a = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO)
    if not a:
        return None
    for t in q("SELECT id, numero, estado, parametros FROM tickets WHERE cliente_id=? AND automatizacion_id=? "
               "AND estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') ORDER BY id DESC LIMIT 50", cliente_id, a["id"]):
        if (j(t["parametros"], {}) or {}).get("al_equipo") == e["nombre"]:
            return t
    return None


def contexto(cliente_id, e, puede_escribir):
    """Todo lo que el botón y el flotante necesitan."""
    t = tipo_de(e)
    if not t:
        return None
    cat = catalogo()
    ap = aplicadas(e)
    act = {x["id"]: x for x in ap.get("alertas") or []}
    grupos = {}
    for a in cat["alertas"]:
        if t in a["tipos"]:
            x = act.get(a["id"])
            accs = [(k, (cat.get("acciones") or {}).get(k, {}).get("texto", k)) for k in (a.get("acciones") or ["avisar"])]
            grupos.setdefault(a.get("grupo", "Otras"), []).append(dict(a, activa=bool(x), acciones_l=accs, valor_accion=(x or {}).get("accion", "avisar"),
                                                                         valor_aviso=(x or {}).get("aviso", a.get("aviso")),
                                                                         valor_critico=(x or {}).get("critico", a.get("critico")),
                                                                         valor_sev=(x or {}).get("severidad", a.get("severidad", "aviso"))))
    auto = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, AUTO)
    disponible, por_que = False, "La automatización «Configurar alertas del equipo» aún no está publicada en AAP"
    if auto:
        from .pruebas import estado
        est = estado(dict(auto, cliente_id=cliente_id))["estado"]
        disponible = est == "certificada"
        por_que = "" if disponible else "La automatización que configura las alertas aún no tiene su plan de prueba aprobado: se podrá activar cuando lo pase"
    if not puede_escribir:
        disponible, por_que = False, "Tu perfil es de solo lectura"
    if not e.get("ip"):
        disponible, por_que = False, "El equipo no tiene IP registrada"
    return {"tipo": t, "tipo_nombre": cat["tipos"][t]["nombre"], "fuente": cat["tipos"][t]["fuente"], "grupos": grupos,
            "n": len(act), "aplicado": ap.get("actualizado"), "ticket": ap.get("ticket"), "resumen": ap.get("resumen") or [],
            "en_curso": en_curso(cliente_id, e), "disponible": disponible, "por_que": por_que, "total": sum(len(v) for v in grupos.values())}


def pedido_de_formulario(e, form):
    """Del flotante → parámetros del ticket (validados contra el catálogo)."""
    t = tipo_de(e)
    cat = catalogo()
    out = []
    for a in cat["alertas"]:
        if t not in a["tipos"] or not form.get(f"on_{a['id']}"):
            continue
        acc = form.get(f"acc_{a['id']}") or "avisar"
        if acc not in (a.get("acciones") or ["avisar"]):
            raise ValueError(f"«{a['nombre']}»: elige una acción de la lista")
        x = {"id": a["id"], "accion": acc}
        if a.get("umbral", True) is False:
            sev = form.get(f"sev_{a['id']}", a.get("severidad", "aviso"))
            x["severidad"] = sev if sev in ("aviso", "critico") else "aviso"
        else:
            for k in ("aviso", "critico"):
                v = (form.get(f"{k}_{a['id']}") or "").strip().replace(",", ".")
                if v:
                    try:
                        x[k] = float(v) if "." in v else int(v)
                    except ValueError:
                        raise ValueError(f"«{a['nombre']}»: el umbral de {k} debe ser un número")
            if "aviso" not in x and "critico" not in x:
                raise ValueError(f"«{a['nombre']}»: indica al menos un umbral (aviso o crítico)")
            if "aviso" in x and "critico" in x and ((x["aviso"] <= x["critico"]) if a.get("menor") else (x["aviso"] >= x["critico"])):
                raise ValueError(f"«{a['nombre']}»: el aviso debe ser {'mayor' if a.get('menor') else 'menor'} que el crítico")
        out.append(x)
    return {"al_equipo": e["nombre"], "al_ip": e.get("ip") or "", "al_tipo": t, "al_alertas": out, "al_retirar": not out}


def registrar_resultado(cliente_id, art, numero):
    """Hook al terminar el ticket: el reflejo de lo aplicado queda en la ficha del equipo."""
    x = (art or {}).get("faceops_alertas") or {}
    e = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente_id, x.get("equipo") or "")
    if not e:
        return
    d = j(e["datos"], {})
    d["alertas"] = {"tipo": x.get("tipo"), "alertas": x.get("alertas") or [], "resumen": x.get("resumen") or [], "actualizado": ahora(), "ticket": numero}
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])


def resumen_cliente(cliente_id):
    """Para Monitoreo › Alertas: cada objeto con alertas configuradas en Ansible."""
    out = []
    for e in q("SELECT id, nombre, descripcion, categoria, so, ip, datos FROM equipos WHERE cliente_id=? AND estado!='retirado' ORDER BY nombre", cliente_id):
        t = tipo_de(e)
        if not t:
            continue
        ap = aplicadas(e)
        out.append({"id": e["id"], "nombre": e["nombre"], "tipo": t, "n": len(ap.get("alertas") or []), "resumen": ap.get("resumen") or [],
                    "actualizado": ap.get("actualizado"), "ticket": ap.get("ticket")})
    return out
