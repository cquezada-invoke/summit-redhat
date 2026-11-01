"""README de cada automatización: la explicación completa (qué hace, cuándo usarla, paso a paso desde su playbook real,
aprobaciones por área, parámetros, qué cambia, riesgo y cómo se deshace, requisitos, datos técnicos, créditos y valor).
Se publica en Git (invoke/faceops-automatizaciones › automatizaciones/<slug>/README.md) y su descripción es la misma que
muestra la ficha de la automatización en FaceOps."""
import base64
import os
import re

import requests
import yaml

from .bd import ej, j, q

REPO = "invoke/faceops-automatizaciones"
CARPETA = "automatizaciones"
RIESGO = {"bajo": "Bajo: no interrumpe servicios.", "medio": "Medio: cambia el entorno de forma controlada.",
          "alto": "Alto: puede afectar servicios en uso; se ejecuta con aprobación y en ventana."}


def _gitea():
    base = os.environ.get("FACEOPS_GITEA_INTERNO", "http://192.168.122.1:3000").rstrip("/")
    tok = os.environ.get("FACEOPS_GITEA_TOKEN", "")
    return base, ({"Authorization": f"token {tok}"} if tok else {})


def slug(t):
    t = (t or "").lower().translate(str.maketrans("áéíóúñü·", "aeiounu-"))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", t)).strip("-")[:60] or "automatizacion"


def _repo_de(url):
    m = re.search(r"/([^/]+)/([^/]+?)(?:\.git)?$", url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def pasos_playbook(tec):
    """[(play, [tareas])] leídos del playbook real en Git (la versión que usa AAP)."""
    if not tec.get("playbook") or not tec.get("repositorio"):
        return []
    dueno, repo = _repo_de(tec["repositorio"])
    if not dueno:
        return []
    base, h = _gitea()
    try:
        r = requests.get(f"{base}/api/v1/repos/{dueno}/{repo}/raw/{tec['playbook']}", headers=h, timeout=8,
                         params={"ref": tec.get("commit") or tec.get("rama") or "main"} if (tec.get("commit") or tec.get("rama") not in (None, "", "por defecto")) else None)
        if not r.ok:
            return []
        doc = yaml.safe_load(r.text) or []
    except (requests.RequestException, yaml.YAMLError):
        return []
    out = []

    def tareas(lista):
        nombres = []
        for t in lista or []:
            if not isinstance(t, dict):
                continue
            if t.get("block"):
                if t.get("name"):
                    nombres.append(t["name"])
                nombres += tareas(t["block"])
            elif t.get("name"):
                nombres.append(str(t["name"]))
            elif t.get("include_tasks") or t.get("ansible.builtin.include_tasks") or t.get("import_tasks") or t.get("ansible.builtin.import_tasks"):
                nombres.append("Incluye: " + str(t.get("include_tasks") or t.get("ansible.builtin.include_tasks") or t.get("import_tasks") or t.get("ansible.builtin.import_tasks")))
        return nombres
    for play in doc if isinstance(doc, list) else []:
        if not isinstance(play, dict):
            continue
        if play.get("import_playbook") or play.get("ansible.builtin.import_playbook"):
            out.append((f"Importa {play.get('import_playbook') or play.get('ansible.builtin.import_playbook')}", []))
            continue
        ts = tareas(play.get("pre_tasks")) + [f"Rol: {r_ if isinstance(r_, str) else r_.get('role') or r_.get('name')}" for r_ in play.get("roles") or []] \
            + tareas(play.get("tasks")) + tareas(play.get("post_tasks"))
        out.append((str(play.get("name") or f"Play sobre {play.get('hosts', '?')}"), ts))
    return out


def _frase(t):
    t = (t or "").strip()
    return (t[0].upper() + t[1:] + ("" if t.endswith((".", "!", "?")) else ".")) if t else ""


def descripcion_larga(a, guia=None, ben=None, pasos=None):
    """La explicación en prosa: lo mismo que abre el README y que muestra la ficha."""
    guia = guia if guia is not None else j(a.get("guia"), {})
    partes = [_frase(guia.get("proposito") or a.get("descripcion") or a.get("nombre"))]
    if guia.get("cuando"):
        partes.append("Se usa " + guia["cuando"][0].lower() + guia["cuando"][1:].rstrip(".") + ".")
    tareas = [t["nombre"] for t in (ben or {}).get("tareas", []) if t.get("nombre") and "sin desglose" not in t["nombre"]]
    if tareas:
        partes.append("Reemplaza el trabajo manual de " + (", ".join(x[0].lower() + x[1:] for x in tareas[:-1]) + " y " if len(tareas) > 1 else "")
                      + tareas[-1][0].lower() + tareas[-1][1:] + ".")
    pasos_n = sum(len(ts) for _p, ts in (pasos or []))
    if pasos_n:
        partes.append(f"En Ansible Automation Platform son {pasos_n} pasos automatizados" + (f" en {len(pasos)} etapas" if len(pasos) > 1 else "") + ".")
    if guia.get("cambios"):
        partes.append(_frase(guia["cambios"]))
    elif a.get("tipo") == "lectura":
        partes.append("No cambia nada en los equipos: solo consulta y reporta.")
    if guia.get("resultado"):
        partes.append("Resultado: " + guia["resultado"][0].lower() + guia["resultado"][1:].rstrip(".") + ".")
    return " ".join(p for p in partes if p)


def markdown(a, cliente_id):
    from .aprobacion_areas import grupos_de, nombre as nom_area, area_de
    from .economia import beneficio
    guia, tec = j(a.get("guia"), {}), j(a.get("tecnico"), {})
    ben = beneficio(a)
    pasos = pasos_playbook(tec)
    desc = descripcion_larga(a, guia, ben, pasos)
    campos = [c for c in j(a.get("campos"), []) if c.get("var")]
    riesgo = guia.get("riesgo") or ("bajo" if a.get("tipo") == "lectura" else "medio")
    L = [f"# {a['nombre']}", "", f"> {a.get('categoria') or ''} · {'solo lectura' if a.get('tipo') == 'lectura' else 'cambia el entorno'} · riesgo {riesgo}"
         f" · plantilla de AAP «{a.get('nombre_aap')}»", "", "## Descripción", "", desc, ""]
    if guia.get("flujo") or guia.get("faceops"):
        L += ["## Contexto", ""] + ([f"- **En la demo:** {guia['flujo']}"] if guia.get("flujo") else []) + ([f"- **Desde FaceOps:** {guia['faceops']}"] if guia.get("faceops") else []) + [""]
    L += ["## Cuándo usarla", "", guia.get("cuando") or "Cuando el área responsable lo requiera, desde el catálogo de FaceOps o desde un ticket.", ""]
    L += ["## Cómo se ejecuta", "", "1. **Solicitud en FaceOps:** se valida el perfil (segmentos de negocio y ambientes) y nace el ticket FO-….",
          "2. **Aprobaciones por área** (no vencen; el tiempo de cada etapa se suma a su área):"]
    for gi, gr in enumerate(grupos_de(a), 1):
        L.append(f"   - Etapa {gi}: " + " y ".join(nom_area(x) for x in gr) + (" (en paralelo)" if len(gr) > 1 else ""))
    L.append("3. **Ejecución en AAP**, desde el nodo de ejecución de la infraestructura donde están los equipos:")
    if pasos:
        for p_, ts in pasos:
            L.append(f"   - **{p_}**")
            L += [f"     {i}. {t}" for i, t in enumerate(ts, 1)]
    else:
        L += [f"   - {t['nombre']}" for t in ben["tareas"]]
    L += ["4. **Cierre:** FaceOps lee el resultado, cierra el ticket, descuenta los créditos y suma el valor generado.", ""]
    L += ["## Tareas que reemplaza", "", "| Tarea | A mano | Automatizada | Alcance |", "|---|---|---|---|"]
    L += [f"| {t['nombre']} | {t['manual']:g} min | {t['auto']:.1f} min | {'por máquina' if t.get('alcance') == 'unidad' else 'por ejecución'} |" for t in ben["tareas"]]
    L += ["", "## Parámetros", ""]
    if campos:
        L += ["| Variable | Pregunta | Tipo | Obligatorio | Valor por defecto | Ayuda |", "|---|---|---|---|---|---|"]
        for c in campos:
            L.append(f"| `{c['var']}` | {c.get('etiqueta', '')} | {c.get('tipo', '')} | {'sí' if c.get('requerido') else 'no'} | {c.get('defecto') or '—'} | "
                     f"{(c.get('ayuda') or '').replace('|', '/') or ('opciones: ' + ', '.join(map(str, c.get('opciones') or [])) if c.get('opciones') else '—')} |")
    else:
        L.append("No pide datos: se ejecuta sobre los equipos elegidos." if a.get("usa_limite") else "No pide datos.")
    L += ["", "## Qué cambia, riesgo y vuelta atrás", "",
          f"- **Qué cambia:** {guia.get('cambios') or ('nada: solo consulta' if a.get('tipo') == 'lectura' else 'ver los pasos de ejecución')}",
          f"- **Riesgo:** {RIESGO.get(riesgo, riesgo)}",
          f"- **Cómo se deshace:** {guia.get('reversa') or 'ver el historial de cambios del equipo en Git (respaldo continuo) y la automatización inversa si existe'}",
          f"- **Resultado esperado:** {guia.get('resultado') or 'ticket exitoso con el detalle de cada paso en su bitácora'}", ""]
    L += ["## Requisitos", "",
          f"- **Área dueña:** {nom_area(area_de(a))}",
          f"- **Inventario de AAP:** {tec.get('inventario') or a.get('inventario') or 'el del equipo elegido'}",
          f"- **Credenciales (en AAP o en la bóveda):** {', '.join(tec.get('credenciales') or []) or 'las del inventario de cada infraestructura'}",
          f"- **Compatible con:** {', '.join(j(a.get('compatibles'), [])) or 'todos los equipos que cumplan las condiciones de la automatización'}", ""]
    L += ["## Datos técnicos", "",
          f"- **Plantilla de AAP:** «{a.get('nombre_aap')}» ({'plantilla' if a.get('aap_tipo') == 'job' else 'workflow'} #{a.get('aap_id')})"]
    if tec.get("playbook"):
        L.append(f"- **Playbook:** `{tec['playbook']}` · repositorio `{tec.get('repositorio')}` · rama {tec.get('rama') or 'main'}")
    L += [f"- **Versión:** {a.get('version') or '—'}", ""]
    L += ["## Créditos y valor", "",
          f"- **Créditos:** {'sin cobro' if ben.get('sin_cobro') else ('%.2f por máquina' % ben['factor_unidad']) + (' + %.2f por ejecución' % ben['factor_ejecucion'] if ben.get('factor_ejecucion') else '')}",
          f"- **Trabajo manual que reemplaza:** ~{a.get('minutos_manual') or ben['manual']:g} min",
          f"- **Más rápido que a mano:** {ben['veces']:.0f} veces (sin contar la espera de aprobación)" if ben.get("veces") else "- **Más rápido que a mano:** sin medir", "",
          "---", "_Generado por FaceOps a partir del catálogo, la guía y el playbook real. Se actualiza al publicar los README._"]
    return desc, "\n".join(L) + "\n"


def _guardar_git(ruta, contenido, mensaje):
    base, h = _gitea()
    dueno, repo = REPO.split("/")
    url = f"{base}/api/v1/repos/{dueno}/{repo}/contents/{ruta}"
    act = requests.get(url, headers=h, timeout=8)
    datos = {"content": base64.b64encode(contenido.encode()).decode(), "message": mensaje,
             "author": {"name": "FaceOps", "email": "faceops@invoke.cl"}}
    if act.ok:
        if base64.b64decode(act.json().get("content") or "").decode(errors="ignore") == contenido:
            return "sin cambios"
        r = requests.put(url, headers=h, json=dict(datos, sha=act.json()["sha"]), timeout=10)
    else:
        r = requests.post(url, headers=h, json=datos, timeout=10)
    return "ok" if r.ok else f"error {r.status_code}: {r.text[:120]}"


def publicar(cliente_id, solo=None):
    """Genera y publica el README de cada automatización visible (o solo de una) y guarda su descripción en FaceOps."""
    autos = q("SELECT * FROM automatizaciones WHERE cliente_id=? AND visible=1" + (" AND id=?" if solo else "") + " ORDER BY categoria, nombre",
              cliente_id, *([solo] if solo else []))
    res, indice = {}, []
    for a in autos:
        try:
            desc, md = markdown(a, cliente_id)
        except Exception as x:  # noqa: BLE001 - una automatización con datos raros no frena al resto
            res[a["nombre"]] = f"error: {str(x)[:100]}"
            continue
        ruta = f"{CARPETA}/{slug(a['nombre_aap'])}/README.md"
        ej("UPDATE automatizaciones SET descripcion_larga=?, readme_ruta=? WHERE id=?", desc, ruta, a["id"])
        res[a["nombre"]] = _guardar_git(ruta, md, f"README de «{a['nombre']}»")
        indice.append((a.get("categoria") or "Otras", a["nombre"], ruta, desc))
    if not solo and indice:
        L = ["# Automatizaciones de FaceOps", "", "Cada automatización tiene su README con la explicación completa.", ""]
        cat = None
        for c, n, r, d in indice:
            if c != cat:
                L += ["", f"## {c}", ""]
                cat = c
            L.append(f"- [{n}]({r.split('/', 1)[1]}) — {d[:160]}{'…' if len(d) > 160 else ''}")
        _guardar_git(f"{CARPETA}/README.md", "\n".join(L) + "\n", "Índice de automatizaciones")
    return res
