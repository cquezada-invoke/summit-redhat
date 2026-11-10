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


FALLAS = set()
ESTADO = "/var/lib/portal-informes/estado_pruebas.yml"   # el portal lo lee en vivo: cada prueba libera (o no) su automatización


def registrar(nombre, ok, nota):
    import yaml
    try:
        d = yaml.safe_load(open(ESTADO)) or {}
    except OSError:
        d = {}
    if ok and nombre in FALLAS:   # otro caso de la misma automatización falló en esta corrida: no se libera
        return
    if not ok:
        FALLAS.add(nombre)
    d[nombre] = {"estado": "probada" if ok else "repetir", "nota": nota}
    with open(ESTADO, "w") as f:
        yaml.safe_dump(d, f, allow_unicode=True)


def pdf_enviado(wj):
    """None si el workflow no tiene paso de informe; True/False según si el PDF salió por correo."""
    for n in aap("GET", f"workflow_jobs/{wj}/workflow_nodes/?page_size=50")["results"]:
        if n.get("identifier") == "informe":
            sj = n["summary_fields"].get("job")
            if not sj:
                return False
            r = urllib.request.Request(C + f"jobs/{sj['id']}/stdout/?format=txt", headers=H)
            with urllib.request.urlopen(r, context=CTX, timeout=60) as x:
                return "enviado a" in x.read().decode(errors="replace")
    return None


def lanzar(nombre, ev=None, limite=None, aprobar=True, completo=None):
    w = aap("GET", "workflow_job_templates/?name=" + urllib.parse.quote(completo or f"Portal · {nombre}"))["results"][0]
    cuerpo = {"extra_vars": ev or {}}
    if limite:
        cuerpo["limit"] = limite
    wj = aap("POST", f"workflow_job_templates/{w['id']}/launch/", cuerpo)["workflow_job"]
    f = esperar_fin(wj, aprobar)
    print(f"== {nombre} {json.dumps(ev or {}, ensure_ascii=False)[:120]} → #{wj} {f['status']}\n{resumen(wj)}", flush=True)
    esperado = "failed" if (not aprobar or (ev or {}).get("simular_falla") == "si") else "successful"
    pdf = pdf_enviado(wj)
    if pdf is not None:
        print(f"     · PDF por correo: {'enviado' if pdf else 'NO ENVIADO'}", flush=True)
    ok = f["status"] == esperado and pdf is not False
    registrar(nombre, ok, f"{time.strftime('%d-%m %H:%M')} · #{wj} {f['status']}" + ("" if ok else (" (revisar)" if pdf is not False else " (PDF no enviado)")))
    return f["status"]


CASOS = {
    "salud": lambda: lanzar("OpenShift · Salud del clúster", {"severidad_minima": "warning", "incluir_pods": "si"}),
    "cert": lambda: lanzar("OpenShift · Certificados", {"dias_alerta": 30, "aprobar_csr": "no"}),
    "actual": lambda: lanzar("OpenShift · Actualizaciones y operadores", {"aprobar_operadores": "no"}),
    "optim": lambda: lanzar("OpenShift · Optimizar recursos y limpiar", {"modo": "revisar", "umbral_ocioso": 20, "dias_antiguedad": 7}),
    "proyecto": lambda: (lanzar("OpenShift · Alta de proyecto", {"proyecto": "demo-pagos", "equipo": "equipo-pagos", "ambiente": "desarrollo", "cpu": 1, "memoria_gi": 1, "accion": "crear"}),
                         lanzar("OpenShift · Regularizar línea base", {"modo": "revisar", "namespaces": ""}),
                         lanzar("OpenShift · Regularizar línea base", {"modo": "corregir", "namespaces": "demo-pagos"}),
                         lanzar("OpenShift · Alta de proyecto", {"proyecto": "demo-pagos", "equipo": "equipo-pagos", "ambiente": "desarrollo", "cpu": 1, "memoria_gi": 1, "accion": "eliminar"})),
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
    # --- automatizaciones nuevas de alto valor -------------------------------------------------------------------------
    "alta": lambda: (lanzar("OpenShift · Alta de proyecto", {"proyecto": "demo-pagos", "equipo": "equipo-pagos", "ambiente": "desarrollo", "cpu": 1, "memoria_gi": 1, "accion": "crear"}),
                     lanzar("OpenShift · Alta de proyecto", {"proyecto": "demo-pagos", "equipo": "equipo-pagos", "ambiente": "desarrollo", "cpu": 1, "memoria_gi": 1, "accion": "eliminar"})),
    "onboarding": lambda: (lanzar("Telco · Onboarding de CNF", {"cnf_nombre": "upf-demo", "proveedor": "nabstract", "vlan_id": 320, "svc_prefijo": "10.60.20.0/24", "hugepages_gi": 4, "accion": "crear"}),
                           lanzar("Telco · Onboarding de CNF", {"cnf_nombre": "upf-demo", "proveedor": "nabstract", "vlan_id": 320, "svc_prefijo": "10.60.20.0/24", "hugepages_gi": 4, "accion": "retirar"})),
    "acceso": lambda: (lanzar("OpenShift · Acceso de equipo", {"equipo": "equipo-demo", "miembros": "demo1,demo2", "namespaces": "amdocs-bss", "rol": "view", "accion": "alta"}),
                       lanzar("OpenShift · Acceso de equipo", {"equipo": "equipo-demo", "miembros": "", "namespaces": "amdocs-bss", "rol": "view", "accion": "baja"})),
    "certapp": lambda: lanzar("OpenShift · Certificado de aplicación de punta a punta", {"namespace": "amdocs-bss", "ruta": "ordering", "umbral_dias": 30, "forzar": "no"}),
    "alerta": lambda: lanzar("OpenShift · De alerta a incidente", {"namespace": "amdocs-bss", "alerta": "KubePodCrashLooping", "remediar": "no"}),
    "infocp": lambda: lanzar("OpenShift · Informe de seguridad del clúster", {"correo": "cquezada@redhat.com", "dias_secretos": 180}),
    "infamd": lambda: lanzar("Amdocs · Informe de operación y conciliación", {"correo": "cquezada@redhat.com", "minutos_atasco": 60}),
    "inflinux": lambda: lanzar("Linux · Informe de seguridad", {"correo": "cquezada@redhat.com"}, limite="nodo-jboss"),
    "disco": lambda: lanzar("Linux · Liberar espacio en disco", {"umbral": 85, "forzar": "no"}, limite="nodo-jboss"),
    "guiada": lambda: lanzar("OpenShift · Actualización guiada con ACM", {"fase": "pre", "version_destino": "", "rutas_criticas": ""}),
}


def orden():
    # igual que el botón «Crear orden y activar» del portal: se crea la orden en Amdocs y se lanza su activación
    o = amdocs("POST", "/orders", {"cliente": "CLI-00001", "oferta": "OFR-5G-EMP", "cnf": "upf", "nombre": "upf-banco", "vlan": 330, "prefijo": "10.60.30.0/24"})
    print(f"== Orden Amdocs {o['id']} creada", flush=True)
    lanzar("Amdocs · Activar orden de servicio", {"orden_id": o["id"]})
    print(f"   orden en Amdocs: {amdocs('GET', '/orders/' + o['id'])['estado']}", flush=True)
    lanzar("Telco · Red del servicio (VLAN y BGP)", {"svc_accion": "retirar", "svc_nombre": "upf-banco", "vlan_id": 330, "svc_prefijo": "10.60.30.0/24", "simular_falla": "no"})
    lanzar("Telco · Onboarding de CNF", {"cnf_nombre": "upf-banco", "proveedor": "nabstract", "vlan_id": 330, "svc_prefijo": "10.60.30.0/24", "hugepages_gi": 4, "accion": "retirar"})


CASOS["orden"] = orden


def demo():
    """Cada automatización visible del portal con los datos que trae precargados el formulario (lo que se usará en la demo)."""
    import yaml
    cat = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalogo.yml")))
    limpiar = {"OpenShift · Alta de proyecto": {"accion": "eliminar"}, "OpenShift · Acceso de equipo": {"accion": "baja", "miembros": ""},
               "Telco · Onboarding de CNF": {"accion": "retirar"}, "Telco · Red del servicio (VLAN y BGP)": {"svc_accion": "retirar"},
               "OpenShift · Actualización guiada con ACM": {"fase": "post"}}
    solo = [a for a in sys.argv if a.startswith("--solo=")]
    for it in cat:
        if it.get("oculto") or it["seccion"] == "infra" or it.get("orden"):
            continue
        if solo and not any(x.split("=", 1)[1].lower() in it["nombre"].lower() for x in solo):
            continue
        ev = {e["variable"]: e["default"] for e in it.get("encuesta") or [] if e.get("default") not in (None, "")}
        lim = ("nodo-windows" if it["nombre"].startswith("Windows") else "nodo-jboss") if it.get("limite") else None
        lanzar(it["nombre"], ev, limite=lim)
        if it["nombre"] in limpiar:   # deja el laboratorio como estaba (y prueba el camino de vuelta)
            lanzar(it["nombre"], dict(ev, **limpiar[it["nombre"]]), limite=lim)


def snow():
    """Las tres que se piden desde ServiceNow: se crea el pedido en el catálogo, EDA lo recibe y AAP lo atiende con aprobación."""
    sys.path.insert(0, os.path.expanduser("~/summit-redhat/demo4-redes/servicenow"))
    from sn import api, uno
    casos = [("Solicitar acceso de red", "Red · Solicitar acceso con aprobación",
              {"origen": "10.40.40.0/24", "destino": "10.10.10.10", "puerto": "8089", "motivo": "Prueba nocturna del portal"}),
             ("Revocar acceso de red", "Red · Revocar acceso con aprobación",
              {"origen": "10.40.40.0/24", "destino": "10.10.10.10", "puerto": "8089", "motivo": "Prueba nocturna: se retira el acceso"}),
             ("Preparar red para clúster OpenShift", "Red · Preparar red OpenShift con aprobación",
              {"cluster": "ocp-prueba", "segmento": "10.60.7.0/24", "motivo": "Prueba nocturna del portal"})]
    for item, wf, vars_ in casos:
        it = uno("sc_cat_item", "name=" + item)
        antes = max([j["id"] for j in aap("GET", "workflow_jobs/?name=" + urllib.parse.quote(wf) + "&order_by=-id&page_size=1")["results"]] or [0])
        r = api("POST", f"/api/sn_sc/servicecatalog/items/{it['sys_id']}/order_now", {"sysparm_quantity": "1", "variables": vars_})
        num = (r.get("result") or {}).get("request_number", "?")
        print(f"== ServiceNow «{item}» pedido {num}; esperando que EDA lance «{wf}»", flush=True)
        wj = None
        for _ in range(60):
            js = aap("GET", "workflow_jobs/?name=" + urllib.parse.quote(wf) + f"&id__gt={antes}&order_by=id&page_size=1")["results"]
            if js:
                wj = js[0]["id"]; break
            time.sleep(5)
        if not wj:
            print("   EDA no lanzó el workflow (revisar vigía y activación)", flush=True)
            registrar(wf, False, f"{time.strftime('%d-%m %H:%M')} · EDA no lanzó el workflow")
            continue
        f = esperar_fin(wj)
        print(f"   workflow #{wj} {f['status']}\n{resumen(wj)}", flush=True)
        registrar(wf, f["status"] == "successful", f"{time.strftime('%d-%m %H:%M')} · #{wj} {f['status']} (desde ServiceNow {num})")


CASOS["demo"] = demo
CASOS["snow"] = snow
args = [a for a in sys.argv[1:] if not a.startswith("--")]
host = sys.argv[sys.argv.index("--host") + 1] if "--host" in sys.argv else None
if host:
    args = [a for a in args if a != host]
    CASOS["infra"] = lambda: (lanzar("Infraestructura · Revisar CVE de servidores", {}, limite=host),
                              lanzar("Infraestructura · Escaneo de seguridad", {}, limite=host),
                              lanzar("Infraestructura · Inventario de certificados", {"cert_dias": 30}, limite=host))
for k in (args or list(CASOS)):
    f = CASOS.get(k)
    if f:
        try:
            f()
        except Exception as x:  # noqa: BLE001
            print(f"== {k}: ERROR {x}", flush=True)
