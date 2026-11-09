"""Gobierno › Fábrica de software › Políticas de software: lo que todo desarrollo debe contener en OpenShift (por ahora Spring Boot). Se editan en
FaceOps (cada cambio es una versión) y las consume el agente de la Fábrica de código, que genera el desarrollo cumpliéndolas;
el pipeline de Tekton verifica las que dicen «verifica: pipeline»."""
import hashlib
import json
import os

import yaml

from .bd import ahora, ej, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "politicas-desarrollo.yml")
AREAS = ["Arquitectura", "Seguridad", "Continuidad operativa", "Infraestructura", "Operación", "Calidad"]   # quién define la política (hacia el desarrollo)
NIVELES = {"obligatoria": "Obligatoria", "recomendada": "Recomendada"}
VERIFICA = {"pipeline": "Pipeline (automática)", "revisión": "Revisión"}
PLATAFORMAS = {"openshift": "OpenShift", "iis": "IIS (.NET)", "jboss": "JBoss EAP", "springboot": "Spring Boot", "quarkus": "Quarkus"}


def aplica(p):
    """Plataformas donde rige la política (lista)."""
    return [x for x in (p.get("aplica") or "openshift").split(",") if x in PLATAFORMAS] or ["openshift"]


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS politicas_desarrollo (id INTEGER PRIMARY KEY, cliente_id INTEGER, codigo TEXT, categoria TEXT, titulo TEXT, "
       "regla TEXT, nivel TEXT, verifica TEXT, area TEXT DEFAULT '', activa INTEGER DEFAULT 1, version INTEGER DEFAULT 1, autor TEXT, fecha TEXT, UNIQUE(cliente_id, codigo))")
    ej("CREATE TABLE IF NOT EXISTS politicas_desarrollo_hist (id INTEGER PRIMARY KEY, cliente_id INTEGER, codigo TEXT, version INTEGER, datos TEXT, "
       "autor TEXT, fecha TEXT, motivo TEXT)")
    from .bd import q as _q
    cols = {r["name"] for r in _q("PRAGMA table_info(politicas_desarrollo)")}
    if "area" not in cols:
        ej("ALTER TABLE politicas_desarrollo ADD COLUMN area TEXT DEFAULT ''")
    if "aplica" not in cols:
        ej("ALTER TABLE politicas_desarrollo ADD COLUMN aplica TEXT DEFAULT ''")


def lista(cliente_id):
    _tabla()
    base = {p["id"]: p for p in yaml.safe_load(open(RUTA, encoding="utf-8")) or []}
    from .bd import ajuste
    if not ajuste(f"politicas_software_v1_{cliente_id}"):   # 09/11: pasan a ser políticas de software para OpenShift (Spring Boot)
        ej("DELETE FROM politicas_desarrollo WHERE cliente_id=? AND codigo LIKE 'PD-%' AND autor='base' AND version=1", cliente_id)
        ajuste(f"politicas_software_v1_{cliente_id}", ahora())
    for p in base.values():   # las políticas base que falten (las nuevas de cada versión) entran; las existentes no se tocan
        ej("INSERT OR IGNORE INTO politicas_desarrollo (cliente_id, codigo, categoria, titulo, regla, nivel, verifica, area, aplica, autor, fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
           cliente_id, p["id"], p["categoria"], p["titulo"], p["regla"], p["nivel"], p.get("verifica", "revisión"), p.get("area", "Arquitectura"),
           ",".join(p.get("aplica") or ["openshift"]), "base", ahora())
    for r in q("SELECT * FROM politicas_desarrollo WHERE cliente_id=? AND autor='base' AND version=1", cliente_id):
        b = base.get(r["codigo"])   # las que nadie editó siguen la versión del producto
        if b and (r["regla"], r["titulo"], r["nivel"]) != (b["regla"], b["titulo"], b["nivel"]):
            ej("UPDATE politicas_desarrollo SET regla=?, titulo=?, nivel=?, verifica=?, categoria=?, area=? WHERE id=?", b["regla"], b["titulo"], b["nivel"],
               b.get("verifica", "revisión"), b["categoria"], b.get("area", "Arquitectura"), r["id"])
    for r in q("SELECT id, codigo FROM politicas_desarrollo WHERE cliente_id=? AND (COALESCE(area,'')='' OR COALESCE(aplica,'')='')", cliente_id):
        b = base.get(r["codigo"]) or {}
        ej("UPDATE politicas_desarrollo SET area=COALESCE(NULLIF(area,''), ?), aplica=COALESCE(NULLIF(aplica,''), ?) WHERE id=?",
           b.get("area") or "Arquitectura", ",".join(b.get("aplica") or ["openshift"]), r["id"])
    orden = {a: i for i, a in enumerate(AREAS)}
    return sorted(q("SELECT * FROM politicas_desarrollo WHERE cliente_id=?", cliente_id), key=lambda p: (orden.get(p["area"], 99), p["categoria"], p["codigo"]))


def guardar(cliente_id, f, autor):
    _tabla()
    cod = (f.get("codigo") or "").strip().upper()
    tit, regla = (f.get("titulo") or "").strip()[:160], (f.get("regla") or "").strip()[:2000]
    if not tit or len(regla) < 15:
        raise ValueError("La política necesita un título y una regla clara")
    nivel = f.get("nivel") if f.get("nivel") in NIVELES else "obligatoria"
    ver = f.get("verifica") if f.get("verifica") in VERIFICA else "revisión"
    cat = (f.get("categoria") or "General").strip()[:60]
    area = f.get("area") if f.get("area") in AREAS else "Arquitectura"
    apl = ",".join(x for x in PLATAFORMAS if x in (f.getlist("aplica") if hasattr(f, "getlist") else (f.get("aplica") or []))) or "openshift"
    actual = q1("SELECT * FROM politicas_desarrollo WHERE cliente_id=? AND codigo=?", cliente_id, cod) if cod else None
    if actual:
        ej("INSERT INTO politicas_desarrollo_hist (cliente_id, codigo, version, datos, autor, fecha, motivo) VALUES (?,?,?,?,?,?,?)", cliente_id, cod,
           actual["version"], json.dumps(actual, ensure_ascii=False), autor, ahora(), (f.get("motivo") or "")[:300])
        ej("UPDATE politicas_desarrollo SET categoria=?, titulo=?, regla=?, nivel=?, verifica=?, area=?, aplica=?, activa=?, version=version+1, autor=?, fecha=? WHERE id=?",
           cat, tit, regla, nivel, ver, area, apl, 0 if f.get("activa") == "0" else 1, autor, ahora(), actual["id"])
        return cod
    n = len(q("SELECT id FROM politicas_desarrollo WHERE cliente_id=?", cliente_id)) + 1
    while q1("SELECT 1 FROM politicas_desarrollo WHERE cliente_id=? AND codigo=?", cliente_id, f"PD-{n:02d}"):
        n += 1
    cod = f"PD-{n:02d}"
    ej("INSERT INTO politicas_desarrollo (cliente_id, codigo, categoria, titulo, regla, nivel, verifica, area, aplica, autor, fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
       cliente_id, cod, cat, tit, regla, nivel, ver, area, apl, autor, ahora())
    return cod


def historial(cliente_id, codigo):
    _tabla()
    return q("SELECT * FROM politicas_desarrollo_hist WHERE cliente_id=? AND codigo=? ORDER BY id DESC", cliente_id, codigo)


def contrato(cliente_id):
    """Lo que recibe el agente: las políticas activas, su versión y una huella del conjunto (cambia cuando cambia cualquier política)."""
    ps = [p for p in lista(cliente_id) if p["activa"]]
    cuerpo = [{**{k: p[k] for k in ("codigo", "area", "categoria", "titulo", "regla", "nivel", "verifica", "version")}, "aplica": aplica(p)} for p in ps]
    huella = hashlib.sha256(json.dumps(cuerpo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {"huella": huella, "fecha": ahora(), "politicas": cuerpo}


def markdown(cliente_id):
    c = contrato(cliente_id)
    lineas = [f"# Políticas de software · OpenShift con Spring Boot (versión {c['huella']})", "",
              "Todo desarrollo debe cumplir estas políticas. Las obligatorias bloquean el despliegue.", ""]
    area = None
    for p in c["politicas"]:
        if p["area"] != area:
            area = p["area"]
            lineas += ["", f"## Área {area}"]
        lineas.append(f"- **{p['codigo']} · {p['titulo']}** [{p['categoria']}] ({p['nivel']}; verifica: {p['verifica']}): {p['regla']}")
    return "\n".join(lineas) + "\n"
