"""Grupos de segmentos de negocio: cuando los segmentos crecen, se ordenan por grupo y cada grupo va en el menú principal.
El grupo se declara en el manifiesto (grupo: redes | aplicaciones | soluciones) o se edita en Gobierno; si falta, se deduce."""
import re

GRUPOS = [("aplicaciones", "Aplicaciones", "apps", "Aplicaciones de negocio con todos sus componentes"),
          ("redes", "Redes", "redes", "Redes corporativas, sucursales, centros de datos y su conectividad"),
          ("soluciones", "Soluciones", "soluciones", "Soluciones transversales: identidades, accesos y servicios compartidos")]
NOMBRE = {k: n for k, n, _, _ in GRUPOS}


def deducir(s):
    texto = f"{s.get('nombre', '')} {s.get('descripcion', '')}".lower()
    if (s.get("sistema") or "") == "red" or re.search(r"\bred(es)?\b|sucursal|firewall|router|switch|wan\b|sd-wan", texto):
        return "redes"
    if re.search(r"identidad|acceso|iga|midpoint|keycloak|sso|active directory|idm", texto):
        return "soluciones"
    return "aplicaciones"


def grupo_de(s):
    g = (s.get("grupo") or "").strip().lower()
    return g if g in NOMBRE else deducir(s)


def normalizar(g):
    g = (g or "").strip().lower()
    alias = {"red": "redes", "aplicacion": "aplicaciones", "aplicación": "aplicaciones", "apps": "aplicaciones",
             "solucion": "soluciones", "solución": "soluciones", "identidades": "soluciones"}
    g = alias.get(g, g)
    return g if g in NOMBRE else ""
