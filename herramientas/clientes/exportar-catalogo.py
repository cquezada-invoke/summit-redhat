#!/usr/bin/python3
"""Exporta del AAP de Invoke Lab el catálogo de acciones de FaceOps (plantillas con su playbook, opciones y encuesta) para que el
instalador de cada cliente lo cargue en su propio AAP. Solo plantillas cuyo playbook está en el repositorio central (faceops/aap).
Se quitan valores que parezcan secretos (claves, tokens) de extra_vars y de los valores por defecto de las encuestas.
Uso (en Invoke Lab): exportar-catalogo.py > catalogo-aap.json"""
import base64, glob, json, os, re, ssl, sys, urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CTX = ssl._create_unverified_context()
INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
AUTH = "Basic " + base64.b64encode(("admin:" + re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()).encode()).decode()
L = "https://localhost/api/controller/v2"
ORGS = {"FaceOps", "Invoke", "Default"}
SECRETO = re.compile(r"(pass|clave|secret|token|key|llave|pwd)", re.I)
CAMPOS = ("description", "job_type", "verbosity", "forks", "timeout", "ask_limit_on_launch", "ask_variables_on_launch", "ask_job_type_on_launch",
          "ask_tags_on_launch", "ask_skip_tags_on_launch", "ask_verbosity_on_launch", "ask_execution_environment_on_launch", "become_enabled",
          "allow_simultaneous", "survey_enabled", "use_fact_cache")


def get(url):
    req = urllib.request.Request(url, headers={"Authorization": AUTH})
    with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def limpiar_vars(txt):
    try:
        d = json.loads(txt or "{}")
    except ValueError:
        import yaml
        d = yaml.safe_load(txt or "") or {}
    if not isinstance(d, dict):
        return "{}"
    return json.dumps({k: ("" if SECRETO.search(k) else v) for k, v in d.items()}, ensure_ascii=False)


repo_pb = {os.path.basename(p) for p in glob.glob(os.path.join(RAIZ, "faceops", "aap", "*.yml"))}
out, url = [], f"{L}/job_templates/?page_size=200"
while url:
    r = get(url)
    for t in r["results"]:
        if ((t.get("summary_fields") or {}).get("organization") or {}).get("name", "") not in ORGS:
            continue
        pb = os.path.basename(t.get("playbook") or "")
        if pb not in repo_pb:
            continue
        x = {"nombre": t["name"], "playbook": "faceops/aap/" + pb, "datos": {k: t.get(k) for k in CAMPOS if k in t}}
        x["datos"]["extra_vars"] = limpiar_vars(t.get("extra_vars"))
        if t.get("survey_enabled"):
            try:
                sv = get(f"{L}/job_templates/{t['id']}/survey_spec/")
                for q in sv.get("spec") or []:
                    if q.get("type") == "password" or SECRETO.search(q.get("variable") or ""):
                        q["default"] = ""
                x["encuesta"] = sv
            except Exception:  # noqa: BLE001
                pass
        out.append(x)
    url = ("https://localhost" + r["next"]) if r.get("next") else None
json.dump({"origen": "Invoke Lab", "plantillas": sorted(out, key=lambda x: x["nombre"])}, sys.stdout, ensure_ascii=False)
print(f"{len(out)} plantillas exportadas", file=sys.stderr)
