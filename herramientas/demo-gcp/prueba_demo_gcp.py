"""Prueba local de la Demo instantánea en GCP de FaceAdmin contra una API de Compute SIMULADA (no toca GCP).

    python3 herramientas/demo-gcp/prueba_demo_gcp.py

Recorre: configuración + clave cifrada (600, fuera de la BD) → probar conexión → crear desde imagen dorada (IP reservada,
etiquetas, anidada, terminationTime) → latido del agente y credenciales de un solo uso → apagado por inactividad →
«encender mi demo» → apagado fuera de horario → apagado por el agente → arranque programado → aviso → vencimiento y
eliminación completa → guarda de etiquetas."""
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

TMP = tempfile.mkdtemp(prefix="fa-demo-gcp-")
os.environ.update(FACEADMIN_BD=os.path.join(TMP, "fa.db"), FACEADMIN_DEMO_GCP_CICLO="0", FACEADMIN_DEMO_GCP_SINCRONO="1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "faceadmin"))

import app as A  # noqa: E402
import demo_gcp as DG  # noqa: E402
import gcp  # noqa: E402

TZ = ZoneInfo("America/Santiago")
T = [datetime(2026, 11, 16, 10, 0, tzinfo=TZ).timestamp()]   # lunes 10:00
DG.RELOJ = lambda: T[0]
OK = []


def chk(cond, txt):
    OK.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + txt)
    if not cond:
        raise SystemExit(f"FALLÓ: {txt}")


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data
        self.content = b"" if data is None else json.dumps(data).encode()
        self.text = self.content.decode()

    def json(self):
        return self._d


class FakeCompute:
    """Simula lo justo de compute/v1: zonas, tipos, imágenes, direcciones, instancias y operaciones."""
    def __init__(self):
        self.inst, self.ips, self.llamadas, self.n = {}, {}, [], 0

    def request(self, metodo, url, headers=None, timeout=None, json=None, params=None):
        assert headers["Authorization"] == "Bearer tok-simulado"
        ruta = url.split("/compute/v1/projects/proyecto-x/", 1)[-1]
        self.llamadas.append((metodo, ruta))
        op = {"name": f"op-{len(self.llamadas)}", "zone": "z" if "/zones/" in "/" + ruta else None, "status": "DONE"}
        m = re.fullmatch(r"regions/[^/]+/addresses(?:/([^/]+))?", ruta)
        if m:
            if metodo == "POST":
                self.n += 1
                self.ips[json["name"]] = {"name": json["name"], "address": f"34.176.10.{self.n}", "labels": json["labels"]}
                return Resp(200, op)
            if m.group(1) not in self.ips:
                return Resp(404, {"error": {"message": "not found"}})
            if metodo == "DELETE":
                if any(i["networkInterfaces"][0]["accessConfigs"][0]["natIP"] == self.ips[m.group(1)]["address"] for i in self.inst.values()):
                    return Resp(400, {"error": {"message": "RESOURCE_IN_USE"}})
                del self.ips[m.group(1)]
                return Resp(200, op)
            return Resp(200, self.ips[m.group(1)])
        m = re.fullmatch(r"zones/[^/]+/instances(?:/([^/]+))?(?:/(start|stop))?", ruta)
        if m:
            n, acc = m.group(1), m.group(2)
            if metodo == "GET" and not n:
                return Resp(200, {"items": list(self.inst.values())})
            if metodo == "POST" and not n:
                self.inst[json["name"]] = dict(json, status="PROVISIONING")
                return Resp(200, op)
            if n not in self.inst:
                return Resp(404, {"error": {"message": "not found"}})
            i = self.inst[n]
            if metodo == "GET":
                if i["status"] == "PROVISIONING":
                    i["status"] = "RUNNING"
                return Resp(200, i)
            if metodo == "DELETE":
                del self.inst[n]
                return Resp(200, op)
            i["status"] = {"start": "RUNNING", "stop": "TERMINATED"}[acc]
            return Resp(200, op)
        if re.fullmatch(r"zones/[^/]+(/machineTypes/[^/]+)?|global/images/family/[^/]+", ruta):
            return Resp(200, {"name": ruta.rsplit("/", 1)[-1]})
        return Resp(404, {"error": {"message": "?"}})


F = FakeCompute()
_orig = gcp.Compute.__init__
gcp.Compute.__init__ = lambda self, cfg=None, sesion=None, token=None: _orig(self, cfg, F, "tok-simulado")

with A.app.app_context():
    UID = A.ej("INSERT INTO usuarios (usuario, nombre, clave, creado) VALUES ('cq', 'Cristian', '!', 'x')")
    cid = A.ej("INSERT INTO clientes (codigo, nombre, url, pais, contacto_correo) VALUES ('acme', 'ACME', 'https://x', 'Chile', 'ti@acme.cl')") \
        if "contacto_correo" in {r["name"] for r in A.q("PRAGMA table_info(clientes)")} else A.ej("INSERT INTO clientes (codigo, nombre, url) VALUES ('acme','ACME','https://x')")
    oid = A.ej("INSERT INTO oportunidades (cliente_id, numero, tipo, creado, por) VALUES (?, 'OPP-CL-2026-0001', 'faceops', 'x', 'cq')", cid)
    did = A.ej("INSERT INTO demos_nube (numero, oportunidad_id, cliente_id, dias, desde, hasta, estado, creado, por) VALUES "
               "('DMO-CL-2026-0007', ?, ?, 14, '2026-11-16', '2026-11-29', 'pedida', '2026-11-16 10:00:00', 'cq')", oid, cid)

cl = A.app.test_client()
with cl.session_transaction() as s:
    s["uid"], s["csrf"] = UID, "c"
A.app.config["SERVER_NAME"] = None

print("1. Configuración y clave de la cuenta de servicio")
r = cl.get("/demos-nube/%d" % did)
chk(r.status_code == 200 and "falta cargar la cuenta de servicio" in r.get_data(as_text=True), "sin clave, la Demo pide configurar GCP")
sa = json.dumps({"type": "service_account", "project_id": "proyecto-x", "client_email": "faceadmin-demos@proyecto-x.iam.gserviceaccount.com",
                 "private_key_id": "abcdef1234567890", "private_key": "clave-falsa-SECRETO-NO-DEBE-VERSE"})
import io  # noqa: E402
r = cl.post("/configuracion/demo-gcp", data={"_csrf": "c", "accion": "clave", "sa": (io.BytesIO(sa.encode()), "sa.json")}, content_type="multipart/form-data")
chk(r.status_code == 302, "clave cargada")
chk(oct(os.stat(gcp.SA_ARCH).st_mode)[-3:] == "600" and oct(os.stat(gcp.LLAVE_ARCH).st_mode)[-3:] == "600", "clave y llave en archivos 600")
crudo = open(gcp.SA_ARCH, "rb").read() + open(A.BD, "rb").read()
chk(b"SECRETO-NO-DEBE-VERSE" not in crudo and b"private_key" not in crudo, "la clave no está en claro ni en el archivo ni en la BD")
cfg = gcp.config()
chk(cfg["proyecto"] == "proyecto-x", "proyecto tomado del JSON")
cl.post("/configuracion/demo-gcp", data=dict({k: str(v) for k, v in cfg.items() if not isinstance(v, bool)}, _csrf="c", accion="config", faceadmin_url="https://fa.ejemplo", arranque_programado=""))
pg = cl.get("/configuracion/demo-gcp").get_data(as_text=True)
chk("faceadmin-demos@proyecto-x" in pg and "SECRETO" not in pg, "la configuración muestra solo la cuenta, nunca la clave")
r = cl.post("/configuracion/demo-gcp", data={"_csrf": "c", "accion": "probar"}, follow_redirects=True)
chk("Conexión correcta" in r.get_data(as_text=True), "probar conexión (API simulada)")

print("2. Crear la demo instantánea")
r = cl.post(f"/demos-nube/{did}/gcp", data={"_csrf": "c", "accion": "crear", "tz": "America/Santiago", "dias_habiles": "1-5", "hora_ini": "8", "hora_fin": "19",
                                            "inactividad_min": "30", "usuario_cliente": "acme", "correo_aviso": "ti@acme.cl"})
with A.app.app_context():
    g_ = A.q1("SELECT * FROM demos_gcp WHERE demo_id=?", did)
    dn = A.q1("SELECT * FROM demos_nube WHERE id=?", did)
nombre = g_["instancia"]
chk(nombre == "demo-dmo-cl-2026-0007" and g_["estado"] == "activa", f"VM {nombre} creada y encendida")
i = F.inst[nombre]
chk(i["labels"]["faceadmin-demo"] == "si" and i["labels"]["expira"].startswith("20261123"), "etiquetas faceadmin-demo y expira")
chk(i["advancedMachineFeatures"]["enableNestedVirtualization"] and i["scheduling"]["instanceTerminationAction"] == "DELETE", "virtualización anidada y tope DELETE en GCP")
chk("global/images/family/faceops-demo" in i["disks"][0]["initializeParams"]["sourceImage"], "desde la imagen dorada (familia)")
meta = {x["key"]: x["value"] for x in i["metadata"]["items"]}
chk(meta["faceadmin-url"] == "https://fa.ejemplo" and g_["url"] == f"https://faceops.{g_['ip']}.sslip.io", "URL sslip.io con la IP reservada")
chk(dn["hasta"] == "2026-11-23" and dn["entorno_url"] == g_["url"], "la Demo queda limitada a 7 días y con su URL")

print("3. Agente de la VM")
chk(cl.post("/api/demo-gcp/DMO-CL-2026-0007/uso", json={"activo": True}, headers={"X-Demo-Token": "malo"}).status_code == 403, "token incorrecto → 403")
H = {"X-Demo-Token": meta["demo-token"]}
r = cl.post("/api/demo-gcp/DMO-CL-2026-0007/uso", json={"activo": True, "sesiones": 1, "http": 3}, headers=H)
chk(r.status_code == 200 and r.get_json()["apagar"] is False, "latido con uso → sigue encendida")
r = cl.get("/api/demo-gcp/DMO-CL-2026-0007/credenciales", headers=H)
chk(r.status_code == 200 and r.get_json()["usuario"] == "acme" and len(r.get_json()["clave"]) >= 12, "credenciales entregadas una vez")
chk(cl.get("/api/demo-gcp/DMO-CL-2026-0007/credenciales", headers=H).status_code == 410, "segunda vez → 410")

print("4. Inactividad")
T[0] += 20 * 60
with A.app.app_context():
    chk(DG.ciclo()[g_["id"]] == "activa", "20 min sin uso → sigue")
T[0] += 11 * 60
r = cl.post("/api/demo-gcp/DMO-CL-2026-0007/uso", json={"activo": False}, headers=H)
chk(r.get_json()["apagar"] and r.get_json()["motivo"] == "sin uso", "el agente recibe la orden de apagarse")
with A.app.app_context():
    chk(DG.ciclo()[g_["id"]] == "apagada" and F.inst[nombre]["status"] == "TERMINATED", "31 min sin uso → FaceAdmin la apaga")

print("5. «Encender mi demo» (enlace público, sin sesión)")
with A.app.app_context():
    tok = A.q1("SELECT token_encender FROM demos_gcp WHERE id=?", g_["id"])["token_encender"]
pub = A.app.test_client()
chk(pub.get(f"/demo/{tok}").status_code == 200, "página pública")
with pub.session_transaction() as s:
    s["csrf"] = "p"
pub.post(f"/demo/{tok}", data={"_csrf": "p"})
chk(F.inst[nombre]["status"] == "RUNNING", "el cliente la enciende en horario hábil")
chk(pub.get("/demo/token-que-no-existe-xxxxxxxx").status_code == 404, "token desconocido → 404")

print("6. Fuera de horario")
T[0] = datetime(2026, 11, 16, 19, 5, tzinfo=TZ).timestamp()
with A.app.app_context():
    chk(DG.ciclo()[g_["id"]] == "apagada", "19:05 → apagada fuera de horario")
r = pub.post(f"/demo/{tok}", data={"_csrf": "p"}, follow_redirects=True)
chk("solo se enciende en horario" in r.get_data(as_text=True) and F.inst[nombre]["status"] == "TERMINATED", "fuera de horario no enciende")
T[0] = datetime(2026, 11, 21, 10, 0, tzinfo=TZ).timestamp()   # sábado
with A.app.app_context():
    try:
        DG.encender(g_["id"], "x")
        chk(False, "sábado no enciende")
    except ValueError:
        chk(True, "sábado no enciende")

print("7. Apagado por el propio agente")
T[0] = datetime(2026, 11, 17, 9, 0, tzinfo=TZ).timestamp()
cl.post(f"/demos-nube/{did}/gcp", data={"_csrf": "c", "accion": "encender"})
F.inst[nombre]["status"] = "TERMINATED"
with A.app.app_context():
    chk(DG.ciclo()[g_["id"]] == "apagada", "la VM se apagó sola → FaceAdmin lo refleja")
    chk(A.q1("SELECT horas_uso FROM demos_gcp WHERE id=?", g_["id"])["horas_uso"] > 0.5, "horas de uso acumuladas")

print("8. Arranque programado")
cl.post(f"/demos-nube/{did}/gcp", data={"_csrf": "c", "accion": "horario", "tz": "America/Santiago", "dias_habiles": "1-5", "hora_ini": "8", "hora_fin": "19",
                                         "inactividad_min": "30", "arranque_programado": "1", "correo_aviso": "ti@acme.cl"})
T[0] = datetime(2026, 11, 18, 8, 1, tzinfo=TZ).timestamp()
with A.app.app_context():
    chk(DG.ciclo()[g_["id"]] == "activa" and F.inst[nombre]["status"] == "RUNNING", "08:01 → se enciende sola")
pg = cl.get(f"/demos-nube/{did}").get_data(as_text=True)
chk("Demo instantánea en GCP" in pg and "Encendida" in pg and tok in pg, "detalle de la Demo con estado, horario y enlace")

print("9. Aviso y vencimiento")
with A.app.app_context():
    exp = A.q1("SELECT expira FROM demos_gcp WHERE id=?", g_["id"])["expira"]
T[0] = exp - 20 * 3600
with A.app.app_context():
    DG.ciclo()
    chk(A.q1("SELECT COUNT(*) n FROM correos WHERE para='ti@acme.cl' AND asunto LIKE '%vence%'")["n"] == 1, "aviso previo enviado (bandeja)")
T[0] = exp + 60
with A.app.app_context():
    chk(DG.ciclo()[g_["id"]] == "eliminando" and nombre not in F.inst, "vencida → VM eliminada")
    chk(DG.ciclo()[g_["id"]] == "eliminada" and nombre not in F.ips, "IP reservada liberada → eliminada")
    x = A.q1("SELECT * FROM demos_gcp WHERE id=?", g_["id"])
    chk(not x["clave_cifrada"] and not x["token_vm_hash"] and not x["token_encender"], "credenciales y tokens borrados")
    chk(A.q1("SELECT estado FROM demos_nube WHERE id=?", did)["estado"] == "cerrada", "la Demo queda cerrada")
    chk(DG.ciclo() == {}, "sin demos vivas no se llama a GCP")

print("10. Guarda: no toca VMs ajenas")
F.inst["demo-ajena"] = {"name": "demo-ajena", "labels": {}, "status": "RUNNING", "networkInterfaces": [{"accessConfigs": [{"natIP": "1.1.1.1"}]}]}
F.inst["prod-db"] = dict(F.inst["demo-ajena"], name="prod-db", labels={"faceadmin-demo": "si"})
for n in ("demo-ajena", "prod-db"):
    try:
        gcp.Compute().apagar(n)
        chk(False, f"{n} no se toca")
    except gcp.ErrorGCP:
        chk(True, f"{n} no se toca (sin etiqueta o sin prefijo)")

print(f"\nOK: {sum(OK)}/{len(OK)} verificaciones")
