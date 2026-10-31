"""Grupos de segmentos de negocio: cuando los segmentos crecen, se ordenan por grupo y cada grupo va en el menú principal.
El grupo se declara en el manifiesto (grupo: redes | aplicaciones | soluciones) o se edita en Gobierno; si falta, se deduce."""
import re

GRUPOS = [("aplicaciones", "Aplicaciones", "apps", "Aplicaciones de negocio con todos sus componentes"),
          ("redes", "Redes", "redes", "Redes corporativas, sucursales, centros de datos y su conectividad"),
          ("soluciones", "Soluciones", "soluciones", "Soluciones transversales: identidades, accesos y servicios compartidos")]
NOMBRE = {k: n for k, n, _, _ in GRUPOS}


def deducir(s):
    nombre = (s.get("nombre") or "").lower()
    texto = f"{nombre} {s.get('descripcion', '')}".lower()
    if re.search(r"identidad|iga\b|midpoint|keycloak|\bsso\b|active directory|\bidm\b", texto):
        return "soluciones"
    # redes por el sistema o el nombre (la descripción de una aplicación puede mencionar su firewall o balanceador)
    if (s.get("sistema") or "") == "red" or re.search(r"\bred(es)?\b|sucursal|wan\b|sd-wan|conectividad", nombre):
        return "redes"
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
