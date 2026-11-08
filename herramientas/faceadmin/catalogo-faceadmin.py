#!/usr/bin/env python3
"""Genera faceadmin/catalogo.json desde faceops/datos/creditos.yml: las automatizaciones que se pueden contratar, con su costo en
créditos por unidad y su categoría (los comentarios «# Categoría» del YAML). FaceAdmin lo usa para el checklist del cobro por alcance."""
import json
import os
import re

import yaml

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
src = os.path.join(R, "faceops", "datos", "creditos.yml")
d = yaml.safe_load(open(src, encoding="utf-8"))
cat, cats = "Infraestructura", {}
for linea in open(src, encoding="utf-8"):
    m = re.match(r"\s{2}#\s*(.+?)\s*$", linea)
    if m and not linea.strip().startswith("# unidad") and ":" not in m.group(1):
        cat = m.group(1)
    m = re.match(r'\s{2}"(.+?)":\s*\{', linea)
    if m:
        cats[m.group(1)] = cat
items = [{"nombre": n, "creditos": float(v.get("creditos") or 0), "unidad": v.get("unidad", "ejecución"), "por_que": v.get("por_que", ""),
          "categoria": cats.get(n, "Otras")} for n, v in (d.get("automatizaciones") or {}).items()
         if float(v.get("creditos") or 0) > 0]
json.dump({"automatizaciones": items}, open(os.path.join(R, "faceadmin", "catalogo.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(items), "automatizaciones ·", len({i['categoria'] for i in items}), "categorías")
