"""Prueba REAL de la «Demo de FaceOps» (crea UNA VM en GCP y la elimina). Corre dentro del contenedor de FaceAdmin:

    podman exec -i faceadmin python3 - < prueba_real_demo.py

1) Oportunidad de prueba → «Demo de FaceOps» (7 días, horario 0–24 todos los días) por la misma ruta que el botón.
2) Acceso: VM RUNNING, https://faceops.<IP>.sslip.io responde con TLS válido, login del cliente en FaceOps y en AWX.
3) Apagado programado: se acota el horario a la hora que ya pasó → FaceAdmin la apaga (TERMINATED en GCP).
4) Vencimiento: se adelanta la expiración → FaceAdmin elimina la VM (y su IP) y cierra la Demo.
5) Limpieza: se borran la oportunidad, la cuenta y la Demo de prueba. Nunca imprime la clave."""
import os
import sys
import time
from datetime import datetime

import requests

os.environ["FACEADMIN_DEMO_GCP_CICLO"] = "0"
sys.path.insert(0, "/opt/faceadmin")
import app as A  # noqa: E402
import demo_gcp as DG  # noqa: E402
import gcp  # noqa: E402

OK, T0 = [], time.time()


def chk(c, t):
    OK.append(bool(c))
    print(f"  {'✓' if c else '✗'} [{round((time.time() - T0) / 60, 1)} min] {t}", flush=True)
    return c


def reg():
    with A.app.app_context():
        return A.q1("SELECT * FROM demos_gcp WHERE demo_id=?", did)


cx = gcp.Compute()
with A.app.app_context():
    u = A.q1("SELECT * FROM usuarios WHERE usuario=?", os.environ.get("FA_USUARIO", "cquezada")) or A.q1("SELECT * FROM usuarios ORDER BY id LIMIT 1")
    cid = A.ej("INSERT INTO clientes (codigo, nombre, url, pais) VALUES ('prueba-demo-gcp', 'Prueba Demo GCP (borrar)', 'https://invoke.cl', 'Chile')")
    oid = A.ej("INSERT INTO oportunidades (cliente_id, numero, tipo, creado, por) VALUES (?, 'OPP-PRUEBA-DEMO-GCP', 'faceops', ?, ?)", cid, A.ahora(), u["usuario"])
cl = A.app.test_client()
with cl.session_transaction() as s:
    s["uid"], s["csrf"] = u["id"], "c"
did = None
try:
    print("1. «Demo de FaceOps» desde la oportunidad", flush=True)
    r = cl.post(f"/oportunidades/{oid}", data={"_csrf": "c", "accion": "demo_faceops", "dias": "7", "tz": "America/Santiago", "dias_habiles": "1-7",
                                               "hora_ini": "0", "hora_fin": "24", "inactividad_min": "60", "usuario_cliente": "cliente"})
    with A.app.app_context():
        d = A.q1("SELECT * FROM demos_nube WHERE oportunidad_id=?", oid)
    did = d and d["id"]
    g = reg()
    chk(r.status_code == 302 and g, f"Demo {d and d['numero']} pedida → VM {g and g['instancia']} · vence {g and DG.local(g, g['expira'])}")
    chk(g and round((g["expira"] - g["creada"]) / 86400) == 7, "vencimiento a 7 días")
    for _ in range(90):
        with A.app.app_context():
            DG.avanzar(g["id"], cx)
        g = reg()
        if g["estado"] == "activa" and g["url"]:
            break
        time.sleep(10)
    chk(g["estado"] == "activa", f"VM RUNNING en GCP ({g['estado']}) · {g['tipo']} · {g['ip']} · {g['error'][:200]}")
    t_vm = time.time() - T0
    i = cx.instancia(g["instancia"])
    chk(i["machineType"].endswith("n2-standard-4") and i["labels"].get("faceadmin-demo") == "si" and
        i.get("scheduling", {}).get("instanceTerminationAction") == "DELETE", "n2-standard-4, etiqueta faceadmin-demo y tope DELETE en GCP")
    print("2. Acceso", flush=True)
    url, lista = g["url"], None
    for _ in range(80):
        try:
            x = requests.get(url + "/ingresar", timeout=10)
            if x.status_code == 200:
                lista = time.time() - T0
                break
        except requests.RequestException:
            pass
        with A.app.app_context():
            DG._act(g["id"], ultimo_uso=time.time())   # que no se apague por inactividad mientras arranca
        time.sleep(15)
    chk(lista, f"{url} responde con TLS válido (VM en {round(t_vm / 60, 1)} min · FaceOps listo en {round((lista or 0) / 60, 1)} min)")
    g = reg()
    clave = gcp.descifrar(g["clave_cifrada"])
    s = requests.Session()
    x = s.post(url + "/ingresar", data={"usuario": g["usuario_cliente"], "clave": clave}, allow_redirects=True, timeout=20)
    chk(x.status_code == 200 and "/ingresar" not in x.url, f"login del cliente en FaceOps → {x.url.replace(url, '')}")
    awx = url.replace("faceops.", "awx.")
    x = requests.get(awx + "/api/v2/me/", auth=(g["usuario_cliente"], clave), timeout=20)
    chk(x.status_code == 200 and x.json()["results"][0]["is_superuser"], f"login del cliente en AWX (api/v2/me → {x.status_code})")
    x = requests.get(awx + "/api/v2/ping/", timeout=20).json()
    chk(x.get("version"), f"AWX comunitario {x.get('version')}")
    chk(g["clave_entregada"], "credenciales entregadas una sola vez a la VM")
    del clave
    print("3. Apagado programado (fuera de horario)", flush=True)
    h = datetime.now(DG._zona("America/Santiago")).hour
    with A.app.app_context():
        DG._act(g["id"], hora_ini=0 if h > 0 else 1, hora_fin=max(1, h), dias_habiles="1-7")
        st = DG.avanzar(g["id"], cx)
    g = reg()
    chk(st == "apagada" and "fuera del horario" in g["motivo_apagado"], f"FaceAdmin la apaga: {g['motivo_apagado']}")
    for _ in range(30):
        if cx.estado(g["instancia"]) == "TERMINATED":
            break
        time.sleep(10)
    chk(cx.estado(g["instancia"]) == "TERMINATED", "GCP: TERMINATED")
    try:
        DG.encender(g["id"], "prueba", cx)
        chk(False, "no se enciende fuera de horario")
    except ValueError as e:
        chk("horario" in str(e), f"no se enciende fuera de horario: {e}")
    print("4. Vencimiento → eliminación", flush=True)
    with A.app.app_context():
        DG._act(g["id"], expira=time.time() - 5)
        DG.avanzar(g["id"], cx)
    for _ in range(40):
        with A.app.app_context():
            st = DG.avanzar(g["id"], cx)
        if st == "eliminada":
            break
        time.sleep(10)
    g = reg()
    with A.app.app_context():
        dn = A.q1("SELECT estado FROM demos_nube WHERE id=?", did)
    chk(g["estado"] == "eliminada" and cx.instancia(g["instancia"]) is None, f"VM eliminada ({g['estado']})")
    chk(dn["estado"] == "cerrada" and not g["clave_cifrada"] and not g["token_vm_hash"], "Demo cerrada, credenciales y tokens borrados")
finally:
    if did:
        g = reg()
        if g and cx.instancia(g["instancia"]) is not None:
            cx.eliminar(g["instancia"])
            print("  · limpieza: VM eliminada", flush=True)
        if g and g["ip"]:
            try:
                cx.liberar_ip(g["instancia"])
            except gcp.ErrorGCP:
                pass
    with A.app.app_context():
        if did:
            A.ej("DELETE FROM demos_gcp WHERE demo_id=?", did)
            A.ej("DELETE FROM demos_nube WHERE id=?", did)
        A.ej("DELETE FROM oportunidades WHERE id=?", oid)
        A.ej("DELETE FROM clientes WHERE id=?", cid)
    print("  · limpieza: oportunidad, cuenta y Demo de prueba borradas", flush=True)
print(f"\nPRUEBA REAL DEMO: {sum(OK)}/{len(OK)} verificaciones OK · {round((time.time() - T0) / 60, 1)} min", flush=True)
