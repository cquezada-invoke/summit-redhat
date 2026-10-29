"""Solicitud de un segmento de negocio nuevo (o de una nueva versión de su arquitectura) como ticket.

El equipo de proyecto adjunta el manifiesto completo. El ticket lanza en AAP el workflow «FaceOps · Nuevo segmento de
negocio», con doble aprobación: primero Arquitectura (la solución) y luego Infraestructura, que antes de aprobar decide
por cada componente si se arma un parque nuevo o si se asigna a máquinas existentes que ya tienen el producto. Al terminar
el workflow, FaceOps aplica esas decisiones al manifiesto, lo publica en Git (Gitea invoke/arquitectura) y activa el
segmento con su plan y su costo."""
import base64
import json
import os
import re

import requests
import yaml

from .arquitectura import (CATEGORIA_TIPO, SISTEMAS, aplicar_segmento, asignacion, costo, hash_de, leer, normalizar, vigente)
from .bd import ahora, auditar, ej, j, q, q1

SEGMENTO_NUEVO = "FaceOps · Nuevo segmento de negocio"
REPO = "invoke/arquitectura"
ETAPAS = {"arquitectura": "Arquitectura", "infraestructura": "Infraestructura"}


def automatizacion(cliente_id):
    return q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente_id, SEGMENTO_NUEVO)


def solicitar(usuario, cliente, texto, motivo=""):
    """Crea la versión propuesta y el ticket (que lanza la doble aprobación en AAP)."""
    from .operacion import solicitar as crear_ticket
    m, err = leer(texto)
    if err:
        raise ValueError("; ".join(err))
    a = automatizacion(cliente["id"])
    if not a:
        raise ValueError("Falta el workflow «FaceOps · Nuevo segmento de negocio» en el catálogo (sincroniza el catálogo)")
    abierta = q1("SELECT t.numero FROM arquitecturas r JOIN tickets t ON t.id=r.ticket_id WHERE r.cliente_id=? AND r.segmento=? "
                 "AND r.estado='propuesta' AND t.estado IN ('en_cola','en_ejecucion','pendiente_aprobacion')", cliente["id"], m["segmento"])
    if abierta:
        raise ValueError(f"Ya hay una solicitud en curso para «{m['segmento']}»: {abierta['numero']}")
    ultima = q1("SELECT MAX(version) v FROM arquitecturas WHERE cliente_id=? AND segmento=?", cliente["id"], m["segmento"])
    v = (ultima["v"] or 0) + 1
    aid = ej("INSERT INTO arquitecturas(cliente_id, segmento, version, contenido, hash, estado, origen, autor, creado) "
             "VALUES (?,?,?,?,?,?,?,?,?)", cliente["id"], m["segmento"], v, texto, hash_de(texto), "propuesta", "solicitud",
             usuario["usuario"], ahora())
    k = costo(m, q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente["id"]))
    nuevo = not vigente(cliente["id"], m["segmento"])
    params = {"segmento": m["segmento"], "arquitectura_id": aid, "version": v, "tipo_solicitud": "nuevo" if nuevo else "nueva versión",
              "area_negocio": m["ficha"].get("area_negocio", ""), "dueno": m["ficha"].get("dueno", ""),
              "criticidad": m["ficha"].get("criticidad", ""), "componentes": len(m["componentes"]),
              "costo_mensual": k["mensual"], "inversion_incremental": k["incremental"], "moneda": k["moneda"]}
    t = crear_ticket(usuario, cliente, a, None, params,
                     motivo or f"{'Nuevo segmento' if nuevo else 'Nueva versión de'} «{m['segmento']}» ({m['ficha'].get('area_negocio', '')})")
    ej("UPDATE arquitecturas SET ticket_id=? WHERE id=?", t["id"], aid)
    auditar(usuario["usuario"], cliente["id"], "solicitar segmento", t["numero"], {"segmento": m["segmento"], "version": v})
    return t


def de_ticket(t):
    aid = j(t.get("parametros"), {}).get("arquitectura_id")
    r = q1("SELECT * FROM arquitecturas WHERE id=?", aid) if aid else None
    if r:
        r["m"] = normalizar(yaml.safe_load(r["contenido"]))
        r["decision_d"] = j(r.get("decision"), {})
    return r


def etapa(aap, t):
    """Etapa de la aprobación pendiente según el nombre del nodo en AAP: arquitectura | infraestructura | None."""
    if t["estado"] != "pendiente_aprobacion" or not t.get("aap_aprobacion_id"):
        return None
    try:
        n = (aap.aprobacion(t["aap_aprobacion_id"]).get("name") or "").lower()
    except Exception:  # noqa: BLE001
        return None
    return "infraestructura" if "infraestructura" in n else ("arquitectura" if "arquitectura" in n else None)


def candidatos(m, eqs, segmentos_de):
    """Por componente y ambiente, las máquinas existentes que Infraestructura puede asignar: mismo sistema y ambiente
    (y que ya tengan el producto, primero), o el equipo de red que cumple ese rol."""
    from .inventario import sistema_eq, skills_de
    out = {}
    for c in m["componentes"]:
        for amb, n in c["replicas"].items():
            if not n:
                continue
            lista = []
            for e in eqs:
                if (e.get("ambiente") or "") != amb and c.get("sistema"):
                    continue
                if c["tipo"] != "servidor" and not c.get("sistema"):
                    if (e.get("categoria") or "servidor") not in CATEGORIA_TIPO.get(c["tipo"], []):
                        continue
                elif (e.get("categoria") or "servidor") != "servidor" or sistema_eq(e) != SISTEMAS.get(c.get("sistema"), ""):
                    continue
                tiene = (not c.get("producto")) or c["producto"] in skills_de(e)
                lista.append({"nombre": e["nombre"], "tiene": tiene, "segmentos": segmentos_de(e), "skills": skills_de(e),
                              "cpu": e.get("cpu"), "ram_gb": round((e.get("ram_mb") or 0) / 1024, 1), "energia": e.get("energia"),
                              "elegido": e["nombre"] in c["equipos"]})
            lista = sorted(lista, key=lambda x: (not x["elegido"], not x["tiene"], len(x["segmentos"]), x["nombre"]))
            con = [x for x in lista if x["tiene"] or x["elegido"]]
            sin = [x for x in lista if not (x["tiene"] or x["elegido"])]
            out[(c["id"], amb)] = con + sin[:2 if con else 4]   # primero las que ya tienen el producto
    return out


def decision_de_formulario(m, form):
    """Formulario de Infraestructura → {componente: {ambiente: {modo, equipos}}}"""
    d = {}
    for c in m["componentes"]:
        for amb, n in c["replicas"].items():
            if not n:
                continue
            modo = form.get(f"modo__{c['id']}__{amb}", "nuevo")
            eqs = form.getlist(f"eq__{c['id']}__{amb}") if modo == "existente" else []
            d.setdefault(c["id"], {})[amb] = {"modo": "existente" if eqs else "nuevo", "equipos": eqs}
    return d


def aplicar_decision(m, decision, eqs, segmento):
    """Manifiesto final: los componentes asignados a máquinas existentes quedan con esos equipos; si alguna pertenece a
    otro segmento, el componente queda compartido (paga solo sus recursos)."""
    from .inventario import segmentos_de as segs_de
    todos_segs = []
    if eqs:
        cid = eqs[0].get("cliente_id")
        todos_segs = q("SELECT * FROM segmentos WHERE cliente_id=?", cid) if cid else []
    por_nombre = {e["nombre"]: e for e in eqs}
    final = json.loads(json.dumps(m))
    for c in final["componentes"]:
        dc = decision.get(c["id"]) or {}
        elegidos = sorted({x for v in dc.values() for x in v.get("equipos", [])})
        if not dc:
            continue
        nuevos = [x for x in elegidos if x not in c["equipos"]]
        c["equipos"] = sorted(set(c["equipos"]) | set(elegidos))
        otros = [x for x in nuevos if x in por_nombre and any(s != segmento for s in segs_de(por_nombre[x], todos_segs))]
        if otros:
            c["uso"] = "compartido"
        c["decision_infraestructura"] = {amb: v["modo"] for amb, v in dc.items()}
    return final


def texto_final(m):
    """YAML del manifiesto aprobado (orden legible, sin los campos internos derivados)."""
    m = json.loads(json.dumps(m))
    for f in m.get("flujos", []):
        f.pop("puertos", None)
        if not f.get("via"):
            f.pop("via", None)
    orden = ["segmento", "codigo", "version", "descripcion", "responsable", "centro_de_datos", "ambientes", "ficha", "entrada",
             "externos", "componentes", "flujos"]
    m = {k: m[k] for k in orden if k in m} | {k: v for k, v in m.items() if k not in orden}
    return yaml.safe_dump(m, allow_unicode=True, sort_keys=False, width=140)


def publicar_git(cliente, segmento, texto, autor, mensaje):
    """Deja el manifiesto aprobado en Gitea (invoke/arquitectura/<segmento>.yml). Devuelve {sha, url, ...} o {}."""
    from .codigo import conf
    base, tok = (conf().get("gitea") or "").rstrip("/"), os.environ.get("FACEOPS_GITEA_TOKEN", "")
    if not base or not tok:
        return {}
    h = {"Authorization": "token " + tok}
    try:
        if requests.get(f"{base}/api/v1/repos/{REPO}", headers=h, timeout=8).status_code == 404:
            requests.post(f"{base}/api/v1/user/repos", headers=h, timeout=8, json={
                "name": REPO.split("/")[1], "private": False, "default_branch": "main", "auto_init": True,
                "description": "Arquitectura aprobada de cada segmento de negocio (FaceOps)"})
        import unicodedata
        plano = unicodedata.normalize("NFKD", segmento.lower()).encode("ascii", "ignore").decode()
        archivo = re.sub(r"[^a-z0-9]+", "-", plano).strip("-") + ".yml"
        actual = requests.get(f"{base}/api/v1/repos/{REPO}/contents/{archivo}", headers=h, timeout=8)
        datos = {"content": base64.b64encode(texto.encode()).decode(), "message": mensaje,
                 "author": {"name": autor, "email": "faceops@invoke.local"}, "branch": "main"}
        if actual.ok:
            r = requests.put(f"{base}/api/v1/repos/{REPO}/contents/{archivo}", headers=h, timeout=8, json=dict(datos, sha=actual.json()["sha"]))
        else:
            r = requests.post(f"{base}/api/v1/repos/{REPO}/contents/{archivo}", headers=h, timeout=8, json=datos)
        r.raise_for_status()
        c = r.json().get("commit") or {}
        return {"sha": c.get("sha", ""), "url": c.get("html_url", ""), "archivo": archivo, "repo": REPO, "autor": autor,
                "fecha": ahora(), "mensaje": mensaje}
    except (requests.RequestException, ValueError, KeyError) as e:
        auditar("sistema", cliente["id"], "error al publicar arquitectura en Git", segmento, {"error": str(e)[:200]})
        return {}


def finalizar(cliente, t, est):
    """Al terminar el workflow: si salió bien, aplica la decisión de Infraestructura, publica en Git y activa el segmento."""
    r = de_ticket(t)
    if not r or r["estado"] != "propuesta":
        return ""
    if est != "exitoso":
        ej("UPDATE arquitecturas SET estado='rechazada', aprobado_en=? WHERE id=?", ahora(), r["id"])
        return f"Solicitud no aprobada: la versión {r['version']} de «{r['segmento']}» quedó rechazada."
    eqs = q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cliente["id"])
    final = aplicar_decision(r["m"], r["decision_d"], eqs, r["segmento"])
    final["version"] = r["version"]
    texto = texto_final(final)
    git = publicar_git(cliente, r["segmento"], texto, "FaceOps (aprobado en AAP)",
                       f"{r['segmento']} v{r['version']} aprobada por Arquitectura e Infraestructura ({t['numero']})")
    ej("UPDATE arquitecturas SET estado='reemplazada' WHERE cliente_id=? AND segmento=? AND estado='aprobada'", cliente["id"], r["segmento"])
    ej("UPDATE arquitecturas SET contenido=?, hash=?, estado='aprobada', aprobado_por=?, aprobado_en=?, git=? WHERE id=?",
       texto, hash_de(texto), "Arquitectura e Infraestructura (AAP)", ahora(), json.dumps(git, ensure_ascii=False), r["id"])
    aplicar_segmento(cliente, normalizar(yaml.safe_load(texto)))
    try:
        from .inventario import calcular
        calcular(cliente)
    except Exception:  # noqa: BLE001
        pass
    k = costo(normalizar(yaml.safe_load(texto)), eqs)
    auditar("FaceOps", cliente["id"], "segmento activado", t["numero"], {"segmento": r["segmento"], "version": r["version"], "git": git.get("sha", "")})
    return (f"Segmento «{r['segmento']}» v{r['version']} activo · costo {k['moneda']} {k['mensual']:,.0f}/mes · inversión incremental "
            f"{k['moneda']} {k['incremental']:,.0f}/mes" + (f" · en Git {REPO}@{git['sha'][:7]}" if git.get("sha") else " · (no se pudo publicar en Git)"))
