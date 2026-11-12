"""Operadores que se agregan a un clúster OpenShift («Agregar plataforma» en la ficha del clúster).

El catálogo vive en datos/operadores-ocp.yml (solo operadores post-instalación, por categoría). La disponibilidad sale de lo
que recolecta AAP con la credencial de solo lectura («OpenShift · Recolectar clúster»): Subscriptions y CSV instalados, y los
packagemanifests del catálogo del clúster para cada paquete de este catálogo. FaceOps no se conecta al clúster."""
import os

import yaml

from .bd import j, q

RAIZ = os.path.dirname(os.path.dirname(__file__))
INSTALAR = "OpenShift · Instalar operador"
DESINSTALAR = "OpenShift · Desinstalar operador"
_cache = {}


def catalogo():
    """[{categoria, operadores: [...]}] tal como está en datos/operadores-ocp.yml (relee si cambió)."""
    p = os.path.join(RAIZ, "datos", "operadores-ocp.yml")
    m = os.path.getmtime(p) if os.path.exists(p) else 0
    if _cache.get("m") != m:
        d = (yaml.safe_load(open(p, encoding="utf-8")) or {}) if m else {}
        _cache.update(m=m, cats=[{"categoria": c.get("nombre") or "", "operadores": [dict(o, categoria=c.get("nombre") or "") for o in c.get("operadores") or []]}
                                 for c in d.get("categorias") or []])
    return _cache["cats"]


def entrada(paquete):
    return next((o for c in catalogo() for o in c["operadores"] if o["paquete"] == paquete), None)


def _gb(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def disponibles(e, cliente_id=None):
    """Catálogo evaluado contra el clúster: cada operador con estado instalado | disponible | no disponible, su motivo y
    sus avisos (recursos del clúster de un solo nodo, storage, dependencias)."""
    d = j(e.get("datos"), {}) if isinstance(e.get("datos"), str) else (e.get("datos") or {})
    ocp = d.get("ocp") or {}
    inst = {o.get("n"): o for o in ocp.get("operadores") or []}
    paq = ocp.get("paquetes") if isinstance(ocp.get("paquetes"), dict) else None
    paq_ok = paq is not None and str(ocp.get("paquetes_completo")).lower() in ("true", "1")
    nodos = len(ocp.get("nodos") or []) or 1
    rec = ocp.get("recursos") or {}
    asig, uso = _gb(rec.get("memoria_asignable_gb")), _gb(rec.get("memoria_uso_gb"))
    libre = round(asig - uso, 1) if asig is not None and uso is not None else None
    clases = (ocp.get("storage") or {}).get("clases") or []
    en_curso = {}
    if cliente_id:
        for t in q("SELECT t.id, t.numero, t.estado, t.parametros, a.nombre_aap FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id "
                   "WHERE t.cliente_id=? AND t.limite=? AND a.nombre_aap IN (?, ?) AND t.estado IN ('pendiente_aprobacion','en_cola','en_ejecucion') ORDER BY t.id",
                   cliente_id, e["nombre"], INSTALAR, DESINSTALAR):
            p_ = j(t["parametros"], {})
            en_curso[p_.get("oi_paquete") or p_.get("od_paquete")] = dict(t, accion="instalar" if t["nombre_aap"] == INSTALAR else "desinstalar")
    out = []
    for c in catalogo():
        ops = []
        for o in c["operadores"]:
            x = dict(o, avisos=[], motivo="", estado="disponible")
            if o.get("nota"):
                x["avisos"].append(o["nota"])
            nombres = [o["paquete"]] + list(o.get("alternativos") or [])
            ya = next((inst[n] for n in nombres if n in inst), None)
            if ya:
                x.update(estado="instalado", instalado=ya, motivo=f"Instalado en {ya.get('ns')} · {ya.get('csv') or 'sin CSV'} · {ya.get('fase') or 'sin estado'}")
                ops.append(x)
                continue
            if o["paquete"] in en_curso:
                t_ = en_curso[o["paquete"]]
                x.update(estado="en curso", ticket=t_, motivo=f"{t_['numero']}: {'instalación' if t_['accion'] == 'instalar' else 'desinstalación'} "
                                                             f"{ {'pendiente_aprobacion': 'esperando aprobación', 'en_cola': 'en cola'}.get(t_['estado'], 'en ejecución') }")
                ops.append(x)
                continue
            pm = None
            if paq is not None:
                pm = next((paq[n] for n in nombres if n in paq), None)
            if paq is not None and pm is None:
                x.update(estado="no disponible", motivo="No existe en el catálogo del clúster" if paq_ok
                         else "No se pudo verificar en el catálogo del clúster (la última recolección no lo leyó)")
                ops.append(x)
                continue
            if pm:
                canales = pm.get("canales") or {}
                if o.get("canal") and o["canal"] not in canales:
                    x["avisos"].append(f"El canal {o['canal']} no está en el catálogo del clúster: se usa {pm.get('canal')}")
                    x["canal"] = pm.get("canal") or ""
                x["fuente"] = pm.get("fuente") or o.get("fuente") or "redhat-operators"
                x["csv"] = canales.get(x.get("canal") or pm.get("canal"), "")
                modos = pm.get("modos") or []
                if modos and o["modo"] not in modos:
                    x.update(estado="no disponible", motivo=f"Su versión del canal {x.get('canal') or pm.get('canal')} no admite {o['modo']} (admite {', '.join(modos)})")
                    ops.append(x)
                    continue
            else:
                x["avisos"].append("Catálogo del clúster sin verificar todavía: la instalación valida el paquete antes de crear nada")
            if (o.get("min_nodos") or 1) > nodos:
                x.update(estado="no disponible", motivo=f"Requiere al menos {o['min_nodos']} nodos y el clúster tiene {nodos}"
                         + (" (un solo nodo)" if nodos == 1 else ""))
                ops.append(x)
                continue
            falta = [r for r in o.get("requiere") or [] if r not in inst]
            if falta:
                x.update(estado="no disponible", motivo="Instala antes " + ", ".join((entrada(r) or {}).get("nombre", r) for r in falta))
                ops.append(x)
                continue
            mem, mem_i = _gb(o.get("memoria_gb")), _gb(o.get("instancia_gb"))
            if libre is not None and mem is not None and mem > libre:
                x.update(estado="no disponible", motivo=f"Sus pods usan unos {mem:g} GB de memoria y el clúster tiene {libre:g} GB libres")
                ops.append(x)
                continue
            if libre is not None and mem_i is not None and mem_i > libre:
                x["avisos"].insert(0, f"Con su instancia usa unos {mem_i:g} GB y el clúster tiene {libre:g} GB libres"
                                      + (" (un solo nodo)" if nodos == 1 else ""))
            if o.get("storage") and not clases:
                x["avisos"].append("El clúster no tiene clase de storage: sus instancias no podrán crear volúmenes")
            ops.append(x)
        out.append({"categoria": c["categoria"], "operadores": ops})
    return out


def resumen(cats):
    ops = [o for c in cats for o in c["operadores"]]
    return {"total": len(ops), "instalados": sum(1 for o in ops if o["estado"] == "instalado"),
            "disponibles": sum(1 for o in ops if o["estado"] == "disponible"),
            "no_disponibles": sum(1 for o in ops if o["estado"] == "no disponible")}


def al_terminar(cliente, ticket, auto_nombre, art):
    """Al terminar bien una instalación o desinstalación, la ficha del clúster queda al día sin esperar la recolección."""
    from .bd import ahora, ej, q1
    import json
    r = (art or {}).get("faceops_operador") or {}
    e = q1("SELECT id, datos FROM equipos WHERE cliente_id=? AND nombre=?", cliente["id"], ticket["limite"]) if ticket.get("limite") else None
    if not r or not e:
        return ""
    d = j(e["datos"], {})
    ocp = d.setdefault("ocp", {})
    ops = [o for o in ocp.get("operadores") or [] if not (o.get("n") == r.get("paquete") and o.get("ns") == r.get("ns"))]
    if auto_nombre == INSTALAR and r.get("fase") == "Succeeded":
        ops.append({"n": r["paquete"], "sub": r.get("sub") or r["paquete"], "ns": r["ns"], "canal": r.get("canal", ""), "aprob": "Manual",
                    "fuente": r.get("fuente", ""), "csv": r.get("csv", ""), "actual": r.get("csv", ""), "nombre": r.get("nombre") or r["paquete"],
                    "fase": "Succeeded", "estado": "AtLatestKnown", "pendientes": [], "pods": int(r.get("pods") or 0),
                    "pods_listos": int(r.get("pods_listos") or 0), "pods_mal": [], "ticket": ticket["numero"], "fecha": ahora()})
    ocp["operadores"] = ops
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])
    return ("\nFicha del clúster: " + (f"{r.get('nombre') or r['paquete']} agregado como plataforma" if auto_nombre == INSTALAR
                                       else f"{r['paquete']} retirado de sus plataformas"))
