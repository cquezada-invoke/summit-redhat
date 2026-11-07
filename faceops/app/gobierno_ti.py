"""Gobierno › Gobierno de TI: el árbol de dependencia (N0 Gobierno de TI → N1 los cinco gobiernos → N2 los frentes →
N3 industria → N4 país) para validar su cumplimiento de a poco. La estructura vive en datos/gobierno-ti.yml; cada
cliente evalúa cada nodo (madurez ACMM 0–5, estado, evidencia y nota) y FaceOps guarda todo su historial."""
import os

import yaml

from .bd import ahora, ej, j, q

RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "gobierno-ti.yml")
ESTADOS = [("sin_evaluar", "Sin evaluar"), ("en_curso", "En revisión"), ("cumple", "Cumple"), ("parcial", "Parcial"), ("no_cumple", "No cumple"), ("no_aplica", "No aplica")]
_cache = {}


def modelo():
    m = os.path.getmtime(RUTA)
    if _cache.get("m") != m:
        _cache.update(m=m, d=yaml.safe_load(open(RUTA, encoding="utf-8")))
    return _cache["d"]


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS gobierno_eval (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nodo TEXT NOT NULL, nivel INTEGER, estado TEXT DEFAULT 'sin_evaluar',
      evidencia TEXT DEFAULT '', nota TEXT DEFAULT '', por TEXT, fecha TEXT);""")


def vigentes(cliente_id):
    """{nodo: última evaluación}"""
    _tabla()
    out = {}
    for r in q("SELECT * FROM gobierno_eval WHERE cliente_id=? ORDER BY id", cliente_id):
        out[r["nodo"]] = r
    return out


def historial(cliente_id, n=15):
    _tabla()
    return q("SELECT * FROM gobierno_eval WHERE cliente_id=? ORDER BY id DESC LIMIT ?", cliente_id, n)


def evaluar(cliente_id, nodo, nivel, estado, evidencia, nota, por):
    if estado not in dict(ESTADOS):
        raise ValueError("Estado inválido")
    if nivel not in (None, "") and not (0 <= int(nivel) <= 5):
        raise ValueError("La madurez va de 0 a 5")
    if nodo not in nodos_validos():
        raise ValueError("Ese nodo no existe en el árbol")
    _tabla()
    ej("INSERT INTO gobierno_eval(cliente_id, nodo, nivel, estado, evidencia, nota, por, fecha) VALUES (?,?,?,?,?,?,?,?)",
       cliente_id, nodo, None if nivel in (None, "") else int(nivel), estado, (evidencia or "")[:1000], (nota or "")[:2000], por, ahora())


def nodos_validos():
    m = modelo()
    out = {"raiz"} | {f"gob:{g['id']}" for g in m["gobiernos"]} | {f"fr:{f['id']}" for f in m["frentes"]} | {f"ban:{b['id']}" for b in m["bandas"]}
    for ind, e in (m.get("especializaciones") or {}).items():
        out |= _rasgos_ids(ind, None, e)
        for pa, ep in (e.get("paises") or {}).items():
            out |= _rasgos_ids(ind, pa, ep)
    return out


def _rasgos_ids(ind, pais, e):
    base = f"esp:{ind}:{pais or '-'}"
    s = {f"{base}:fr:{fr}:{i}" for fr, x in (e.get("frentes") or {}).items() for i in range(len(x.get("suma") or []))}
    s |= {f"{base}:ban:{b}:{i}" for b, xs in (e.get("bandas") or {}).items() for i in range(len(xs or []))}
    return s


def vista(cliente_id, industria, pais):
    """El árbol con la evaluación vigente de cada nodo y el avance por frente (madurez de N2 y de sus rasgos N3/N4)."""
    m = modelo()
    ev = vigentes(cliente_id)
    esp = (m.get("especializaciones") or {}).get(industria) or {}
    esp_p = (esp.get("paises") or {}).get(pais) if esp else None

    def nodo(nid):
        e = ev.get(nid) or {}
        return {"id": nid, "nivel": e.get("nivel"), "estado": e.get("estado") or "sin_evaluar", "evidencia": e.get("evidencia") or "",
                "nota": e.get("nota") or "", "por": e.get("por"), "fecha": e.get("fecha")}

    def rasgos(kind, clave, capa, ind_, pais_):
        xs = (capa.get("frentes" if kind == "fr" else "bandas") or {}).get(clave) if capa else None
        if not xs:
            return None
        items = xs["suma"] if kind == "fr" else xs
        base = f"esp:{ind_}:{pais_ or '-'}:{kind}:{clave}"
        return {"items": [dict(nodo(f"{base}:{i}"), texto=t) for i, t in enumerate(items)],
                "referencia": xs.get("referencia") if kind == "fr" else None, "rasgos": xs.get("rasgos") if kind == "fr" else None}

    contrib = (esp_p or {}).get("contribucion") or {}
    frentes = []
    for f in m["frentes"]:
        x = dict(f, ev=nodo(f"fr:{f['id']}"), ind=rasgos("fr", f["id"], esp, industria, None), pais=rasgos("fr", f["id"], esp_p, industria, pais),
                 aporte=[c for c in contrib.get("filas") or [] if f["id"] in (c.get("frentes") or [])])
        frentes.append(x)
    bandas = [dict(b, ev=nodo(f"ban:{b['id']}"), ind=rasgos("ban", b["id"], esp, industria, None), pais=rasgos("ban", b["id"], esp_p, industria, pais))
              for b in m["bandas"]]
    gobiernos = [dict(g, ev=nodo(f"gob:{g['id']}")) for g in m["gobiernos"]]
    # avance: nodos evaluados y madurez media de lo evaluado
    todos = [nodo("raiz")] + [g["ev"] for g in gobiernos] + [f["ev"] for f in frentes] + [b["ev"] for b in bandas]
    for x in frentes + bandas:
        for k in ("ind", "pais"):
            if x.get(k):
                todos += x[k]["items"]
    evaluados = [t for t in todos if t["estado"] != "sin_evaluar"]
    niveles = [t["nivel"] for t in todos if t["nivel"] is not None]
    cumple = [t for t in evaluados if t["estado"] == "cumple"]
    return {"m": m, "raiz": dict(m["raiz"], ev=nodo("raiz")), "gobiernos": gobiernos, "frentes": frentes, "bandas": bandas,
            "esp": esp, "esp_p": esp_p, "contrib": contrib, "industria": industria, "pais": pais,
            "avance": {"total": len(todos), "evaluados": len(evaluados), "cumple": len(cumple),
                       "pct": round(100 * len(evaluados) / len(todos)) if todos else 0,
                       "madurez": round(sum(niveles) / len(niveles), 1) if niveles else None},
            "estados": ESTADOS}
