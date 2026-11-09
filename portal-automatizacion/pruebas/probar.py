#!/usr/bin/python3
"""Plan de prueba del portal: lanza cada workflow «Portal · …» como lo haría el portal (encuesta + límite), aprueba con la regla
de Cristian, espera el final y muestra el resultado de cada paso. Uso: python3 probar.py [filtro-de-casos] [--host <servidor>]"""
import base64, glob, json, os, re, ssl, sys, time, urllib.parse, urllib.request

INV = glob.glob(os.path.expanduser("~/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo"))[0]
CL = re.search(r"^gateway_admin_password=(.*)$", open(INV).read(), re.M).group(1).strip()
H = {"Authorization": "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode(), "Content-Type": "application/json"}
CTX = ssl._create_unverified_context()
C = "https://localhost/api/controller/v2/"


def aap(m, p, d=None):
    r = urllib.request.Request(C + p, method=m, data=json.dumps(d).encode() if d is not None else None, headers=H)
    with urllib.request.urlopen(r, context=CTX, timeout=60) as x:
        b = x.read()
        return json.loads(b) if b else {}


def amdocs(m, p, d=None):
    r = urllib.request.Request("https://amdocs-ordering.sno.34.176.42.56.sslip.io" + p, method=m,
                               data=json.dumps(d).encode() if d is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, context=CTX, timeout=30) as x:
        return json.loads(x.read())


def esperar_fin(wj, aprobar=True, tope=900):
    t0 = time.time(); aprobados = set()
    while time.time() - t0 < tope:
        w = aap("GET", f"workflow_jobs/{wj}/")
        for n in aap("GET", f"workflow_jobs/{wj}/workflow_nodes/?page_size=50")["results"]:   # la aprobación de ESTE workflow
            sj = n["summary_fields"].get("job") or {}
            if sj.get("type") == "workflow_approval" and sj.get("status") == "pending" and sj["id"] not in aprobados:
                aap("POST", f"workflow_approvals/{sj['id']}/{'approve' if aprobar else 'deny'}/", {}); aprobados.add(sj["id"])
        if w["status"] in ("successful", "failed", "canceled", "error"):
            return w
        time.sleep(4)
    return {"status": "tiempo agotado", "id": wj}


def resumen(wj):
    out = []
    for n in aap("GET", f"workflow_jobs/{wj}/workflow_nodes/?page_size=50&order_by=id")["results"]:
        sj = n["summary_fields"].get("job")
        if not sj or n["summary_fields"].get("unified_job_template", {}).get("unified_job_type") != "job":
            continue
        j = aap("GET", f"jobs/{sj['id']}/")
        r = (j.get("artifacts") or {}).get("portal_resultado")
        lin = f"     · {n['summary_fields']['unified_job_template']['name'][:55]} → {sj['status']}"
        if r:
            lin += f" · {r.get('titulo')} [{r.get('estado')}]"
            lin += "".join(f"\n         {' | '.join(str(c) for c in f)[:150]}" for f in (r.get("filas") or [])[:8])
        if sj["status"] == "failed":
            ev = aap("GET", f"jobs/{sj['id']}/job_events/?event=runner_on_failed&page_size=3")["results"]
            for e in ev:
                res = e.get("event_data", {}).get("res", {})
                lin += f"\n         FALLA en «{e['task']}»: {str(res.get('msg') or res.get('stderr') or res)[:300]}"
        out.append(lin)
    return "\n".join(out)


def lanzar(nombre, ev=None, limite=None, aprobar=True):
    w = aap("GET", "workflow_job_templates/?name=" + urllib.parse.quote(f"Portal · {nombre}"))["results"][0]
    cuerpo = {"extra_vars": ev or {}}
    if limite:
        cuerpo["limit"] = limite
    wj = aap("POST", f"workflow_job_templates/{w['id']}/launch/", cuerpo)["workflow_job"]
    f = esperar_fin(wj, aprobar)
    print(f"== {nombre} {json.dumps(ev or {}, ensure_ascii=False)[:120]} → #{wj} {f['status']}\n{resumen(wj)}", flush=True)
    return f["status"]


CASOS = {
    "salud": lambda: lanzar("OpenShift · Salud del clúster", {"severidad_minima": "warning", "incluir_pods": "si"}),
    "cert": lambda: lanzar("OpenShift · Certificados", {"dias_alerta": 30, "aprobar_csr": "no"}),
    "actual": lambda: lanzar("OpenShift · Actualizaciones y operadores", {"aprobar_operadores": "no"}),
    "optim": lambda: lanzar("OpenShift · Optimizar recursos y limpiar", {"modo": "revisar", "umbral_ocioso": 20, "dias_antiguedad": 7}),
    "proyecto": lambda: (lanzar("OpenShift · Alta de proyecto", {"proyecto": "demo-pagos", "equipo": "equipo-pagos", "ambiente": "desarrollo", "cpu": 1, "memoria_gi": 1, "accion": "crear"}),
                         lanzar("OpenShift · Regularizar línea base", {"modo": "revisar", "namespaces": ""}),
                         lanzar("OpenShift · Regularizar línea base", {"modo": "corregir", "namespaces": "demo-pagos"}),
                         lanzar("OpenShift · Alta de proyecto", {"proyecto": "demo-pagos", "accion": "eliminar"})),
    "rechazo": lambda: lanzar("OpenShift · Salud del clúster", {"severidad_minima": "critical", "incluir_pods": "no"}, aprobar=False),
    "etcd": lambda: lanzar("OpenShift · Respaldo de etcd", {"conservar": 2}),
    "cnf": lambda: (lanzar("Telco · Validar clúster para CNF", {"cnf_perfil": "upf"}),
                    lanzar("Telco · Onboarding de CNF", {"cnf_nombre": "upf-demo", "proveedor": "nabstract", "vlan_id": 320, "svc_prefijo": "10.60.20.0/24", "hugepages_gi": 4, "accion": "crear"}),
                    lanzar("Telco · Onboarding de CNF", {"cnf_nombre": "upf-demo", "proveedor": "nabstract", "vlan_id": 320, "svc_prefijo": "10.60.20.0/24", "hugepages_gi": 4, "accion": "retirar"})),
    "red": lambda: (lanzar("Telco · Red del servicio (VLAN y BGP)", {"svc_accion": "activar", "svc_nombre": "upf-demo", "vlan_id": 320, "svc_prefijo": "10.60.20.0/24", "simular_falla": "no"}),
                    lanzar("Telco · Red del servicio (VLAN y BGP)", {"svc_accion": "retirar", "svc_nombre": "upf-demo", "vlan_id": 320, "svc_prefijo": "10.60.20.0/24", "simular_falla": "no"}),
                    lanzar("Telco · Red del servicio (VLAN y BGP)", {"svc_accion": "activar", "svc_nombre": "upf-falla", "vlan_id": 321, "svc_prefijo": "10.60.21.0/24", "simular_falla": "si"})),
    "dia2": lambda: lanzar("Telco · Día 2 del clúster de cargas"),
    "amdsalud": lambda: lanzar("Amdocs · Salud de la plataforma BSS", {"umbral_ms": 200}),
    "release": lambda: (lanzar("Amdocs · Desplegar release", {"servicio": "catalog", "version": "2.4.0", "simular_falla": "no"}),
                        lanzar("Amdocs · Desplegar release", {"servicio": "catalog", "version": "2.5.0", "simular_falla": "si", }),
                        lanzar("Amdocs · Desplegar release", {"servicio": "catalog", "version": "2.3.0", "simular_falla": "no"})),
}


def orden():
    o = amdocs("POST", "/orders", {"cliente": "CLI-00001", "oferta": "OFR-5G-EMP", "cnf": "upf", "nombre": "upf-banco", "vlan": 330, "prefijo": "10.60.30.0/24"})
    print(f"== Orden Amdocs {o['id']} creada; esperando a EDA", flush=True)
    wj = None
    for _ in range(40):
        for j in aap("GET", "workflow_jobs/?name__icontains=Activar%20orden&order_by=-id&page_size=5")["results"]:
            if json.loads(j.get("extra_vars") or "{}").get("orden_id") == o["id"]:
                wj = j["id"]
        if wj:
            break
        time.sleep(3)
    if not wj:
        print("   EDA no lanzó el workflow"); return
    f = esperar_fin(wj)
    print(f"   workflow #{wj} {f['status']}\n{resumen(wj)}\n   orden en Amdocs: {amdocs('GET', '/orders/' + o['id'])['estado']}", flush=True)
    lanzar("Telco · Red del servicio (VLAN y BGP)", {"svc_accion": "retirar", "svc_nombre": "upf-banco", "vlan_id": 330, "svc_prefijo": "10.60.30.0/24", "simular_falla": "no"})
    lanzar("Telco · Onboarding de CNF", {"cnf_nombre": "upf-banco", "vlan_id": 330, "svc_prefijo": "10.60.30.0/24", "accion": "retirar"})


CASOS["orden"] = orden
args = [a for a in sys.argv[1:] if not a.startswith("--")]
host = sys.argv[sys.argv.index("--host") + 1] if "--host" in sys.argv else None
if host:
    args = [a for a in args if a != host]
    CASOS["infra"] = lambda: (lanzar("Infraestructura · Revisar CVE de servidores", {}, limite=host),
                              lanzar("Infraestructura · Escaneo de seguridad", {}, limite=host),
                              lanzar("Infraestructura · Inventario de certificados", {"cert_dias": 30}, limite=host))
for k, f in CASOS.items():
    if not args or k in args:
        try:
            f()
        except Exception as x:  # noqa: BLE001
            print(f"== {k}: ERROR {x}", flush=True)
