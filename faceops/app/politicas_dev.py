"""Gobierno › Arquitectura › Políticas de desarrollo: lo que todo código debe cumplir para correr en OpenShift. Se editan en
FaceOps (cada cambio es una versión) y las consume el agente de la Fábrica de código, que genera el desarrollo cumpliéndolas;
el pipeline de Tekton verifica las que dicen «verifica: pipeline»."""
import hashlib
import json
import os

import yaml

from .bd import ahora, ej, q, q1

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "politicas-desarrollo.yml")
AREAS = ["Arquitectura", "Infraestructura", "Seguridad", "Operación", "Calidad"]   # quién define la política (hacia el desarrollo)
NIVELES = {"obligatoria": "Obligatoria", "recomendada": "Recomendada"}
VERIFICA = {"pipeline": "Pipeline (automática)", "revisión": "Revisión"}


def _tabla():
    ej("CREATE TABLE IF NOT EXISTS politicas_desarrollo (id INTEGER PRIMARY KEY, cliente_id INTEGER, codigo TEXT, categoria TEXT, titulo TEXT, "
       "regla TEXT, nivel TEXT, verifica TEXT, area TEXT DEFAULT '', activa INTEGER DEFAULT 1, version INTEGER DEFAULT 1, autor TEXT, fecha TEXT, UNIQUE(cliente_id, codigo))")
    ej("CREATE TABLE IF NOT EXISTS politicas_desarrollo_hist (id INTEGER PRIMARY KEY, cliente_id INTEGER, codigo TEXT, version INTEGER, datos TEXT, "
       "autor TEXT, fecha TEXT, motivo TEXT)")
    from .bd import q as _q
    if "area" not in {r["name"] for r in _q("PRAGMA table_info(politicas_desarrollo)")}:
        ej("ALTER TABLE politicas_desarrollo ADD COLUMN area TEXT DEFAULT ''")


def lista(cliente_id):
    _tabla()
    if not q1("SELECT 1 FROM politicas_desarrollo WHERE cliente_id=?", cliente_id):
        for p in yaml.safe_load(open(RUTA, encoding="utf-8")) or []:
            ej("INSERT OR IGNORE INTO politicas_desarrollo (cliente_id, codigo, categoria, titulo, regla, nivel, verifica, area, autor, fecha) VALUES (?,?,?,?,?,?,?,?,?,?)",
               cliente_id, p["id"], p["categoria"], p["titulo"], p["regla"], p["nivel"], p.get("verifica", "revisión"), p.get("area", "Arquitectura"), "base", ahora())
    base = {p["id"]: p.get("area") for p in yaml.safe_load(open(RUTA, encoding="utf-8")) or []}
    for r in q("SELECT id, codigo FROM politicas_desarrollo WHERE cliente_id=? AND COALESCE(area,'')=''", cliente_id):
        ej("UPDATE politicas_desarrollo SET area=? WHERE id=?", base.get(r["codigo"]) or "Arquitectura", r["id"])
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
    actual = q1("SELECT * FROM politicas_desarrollo WHERE cliente_id=? AND codigo=?", cliente_id, cod) if cod else None
    if actual:
        ej("INSERT INTO politicas_desarrollo_hist (cliente_id, codigo, version, datos, autor, fecha, motivo) VALUES (?,?,?,?,?,?,?)", cliente_id, cod,
           actual["version"], json.dumps(actual, ensure_ascii=False), autor, ahora(), (f.get("motivo") or "")[:300])
        ej("UPDATE politicas_desarrollo SET categoria=?, titulo=?, regla=?, nivel=?, verifica=?, area=?, activa=?, version=version+1, autor=?, fecha=? WHERE id=?",
           cat, tit, regla, nivel, ver, area, 0 if f.get("activa") == "0" else 1, autor, ahora(), actual["id"])
        return cod
    n = len(q("SELECT id FROM politicas_desarrollo WHERE cliente_id=?", cliente_id)) + 1
    while q1("SELECT 1 FROM politicas_desarrollo WHERE cliente_id=? AND codigo=?", cliente_id, f"PD-{n:02d}"):
        n += 1
    cod = f"PD-{n:02d}"
    ej("INSERT INTO politicas_desarrollo (cliente_id, codigo, categoria, titulo, regla, nivel, verifica, area, autor, fecha) VALUES (?,?,?,?,?,?,?,?,?,?)",
       cliente_id, cod, cat, tit, regla, nivel, ver, area, autor, ahora())
    return cod


def historial(cliente_id, codigo):
    _tabla()
    return q("SELECT * FROM politicas_desarrollo_hist WHERE cliente_id=? AND codigo=? ORDER BY id DESC", cliente_id, codigo)


def contrato(cliente_id):
    """Lo que recibe el agente: las políticas activas, su versión y una huella del conjunto (cambia cuando cambia cualquier política)."""
    ps = [p for p in lista(cliente_id) if p["activa"]]
    cuerpo = [{k: p[k] for k in ("codigo", "area", "categoria", "titulo", "regla", "nivel", "verifica", "version")} for p in ps]
    huella = hashlib.sha256(json.dumps(cuerpo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return {"huella": huella, "fecha": ahora(), "politicas": cuerpo}


def markdown(cliente_id):
    c = contrato(cliente_id)
    lineas = [f"# Políticas de desarrollo (versión {c['huella']})", "",
              "Todo desarrollo debe cumplir estas políticas. Las obligatorias bloquean el despliegue.", ""]
    area = None
    for p in c["politicas"]:
        if p["area"] != area:
            area = p["area"]
            lineas += ["", f"## Área {area}"]
        lineas.append(f"- **{p['codigo']} · {p['titulo']}** [{p['categoria']}] ({p['nivel']}; verifica: {p['verifica']}): {p['regla']}")
    return "\n".join(lineas) + "\n"
