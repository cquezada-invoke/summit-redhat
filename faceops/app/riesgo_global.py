"""Estado de riesgo del cliente para el indicador central de la cabecera: ¿hay algo crítico ahora?
Mira (1) los segmentos de negocio críticos o de impacto alto con equipos caídos, (2) las alertas de seguridad y de
Event-Driven Ansible de las últimas 24 h (red vulnerada, activaciones caídas, trabajos de remediación fallidos) y
(3) los hallazgos críticos abiertos. Se calcula como mucho una vez por minuto por cliente."""
import json
import time

from .bd import j, q

IMPACTO = {"crítica": 4, "critica": 4, "crítico": 4, "critico": 4, "alta": 3, "alto": 3, "media": 2, "medio": 2, "baja": 1, "bajo": 1}
_cache = {}


def impacto(seg):
    f = seg.get("ficha")
    f = j(f, {}) if isinstance(f, str) else (f or {})
    return IMPACTO.get(str(f.get("criticidad") or "").strip().lower(), 0)


def calcular(cliente, usuario, equipos_vis, url_for):
    k = cliente["id"]
    if k in _cache and time.time() - _cache[k][0] < 60:
        return _cache[k][1]
    from .permisos import segmentos_visibles
    items = []
    # 1. segmentos de negocio críticos o de impacto alto con equipos caídos
    sin_crit = 0
    for s in segmentos_visibles(usuario, cliente["id"]):
        imp = impacto(s)
        if not imp:
            sin_crit += 1
            continue
        if imp < 3:
            continue
        eqs = [e for e in equipos_vis if s["nombre"] in (e.get("segmentos") or [])]
        caidos = [e["nombre"] for e in eqs if e.get("energia") == "apagado"]
        if caidos:
            items.append({"nivel": "critica" if imp == 4 else "alta",
                          "texto": f"{s['nombre']} ({'crítico' if imp == 4 else 'impacto alto'}): {len(caidos)} de {len(eqs)} equipos caídos · {', '.join(caidos[:3])}{'…' if len(caidos) > 3 else ''}",
                          "url": url_for("consola.mapa", vista="aplicaciones", segmento=s["nombre"])})
    # 2. seguridad y operación: alertas de las últimas 24 h
    try:
        from .alertas import leer
        res = leer(cliente, 1)
        al = res.get("alertas", res) if isinstance(res, dict) else res
        vistos = set()
        for a in (al or []):
            if a.get("resuelta") or a.get("estado") == "resuelta" or a.get("nivel") not in ("critica", "alta"):
                continue
            seg_ = any(p in (a.get("titulo", "") + a.get("detalle", "")).lower() for p in ("ataque", "intrus", "seguridad", "vulner", "acs", "ids", "malware", "escaneo", "brute", "ddos"))
            if not seg_ and a.get("origen") not in ("eda", "eda-activacion"):
                continue   # los trabajos fallidos sueltos quedan en Monitoreo › Alertas; aquí solo seguridad y eventos
            txt = ("Seguridad: " if seg_ else "Evento: ") + a.get("titulo", "alerta")
            if txt in vistos:
                continue
            vistos.add(txt)
            items.append({"nivel": "critica" if seg_ or a.get("nivel") == "critica" else "alta", "texto": txt, "url": url_for("consola.alertas")})
    except Exception:  # noqa: BLE001 - sin AAP el indicador sigue con lo demás
        pass
    # 3. hallazgos críticos abiertos
    try:
        ids = [e["id"] for e in equipos_vis]
        if ids:
            n = q(f"SELECT COUNT(*) n FROM hallazgos WHERE cliente_id=? AND lower(severidad) IN ('critica','crítica','critical') AND equipo_id IN ({','.join('?' * len(ids))})",
                  cliente["id"], *ids)[0]["n"]
            if n:
                items.append({"nivel": "alta", "texto": f"{n} hallazgo{'s' if n != 1 else ''} crítico{'s' if n != 1 else ''} de seguridad abiertos",
                              "url": url_for("consola.politicas")})
    except Exception:  # noqa: BLE001
        pass
    if sin_crit:
        items.append({"nivel": "info", "texto": f"{sin_crit} segmento{'s' if sin_crit != 1 else ''} de negocio sin impacto definido", "url": url_for("consola.mapa")})
    orden = {"critica": 0, "alta": 1, "info": 2}
    items.sort(key=lambda x: orden.get(x["nivel"], 3))
    est = "critico" if any(i["nivel"] == "critica" for i in items) else ("aviso" if any(i["nivel"] == "alta" for i in items) else "ok")
    out = {"estado": est, "items": items[:12], "n": sum(1 for i in items if i["nivel"] != "info")}
    _cache[k] = (time.time(), out)
    return out
