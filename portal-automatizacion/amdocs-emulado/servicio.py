"""Amdocs emulado (demo Claro PR): microservicios BSS mínimos para mostrar automatizaciones con Ansible sin software Amdocs.
Un solo programa, tres roles (variable ROL): ordering (órdenes de servicio, avisa a EDA), customer (clientes), catalog (ofertas).
Solo stdlib. Endpoints comunes: /health, /version, /metrics. ordering: GET/POST /orders, GET/PATCH /orders/<id>.
VERSION: versión que informa. FALLA=si: la versión no queda sana (para la demo de vuelta atrás de un release).
EDA_WEBHOOK: URL a la que ordering avisa cada orden nueva (opcional)."""
import json
import os
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROL = os.environ.get("ROL", "ordering")
VERSION = os.environ.get("VERSION", "1.0.0")
FALLA = os.environ.get("FALLA", "no") == "si"
WEBHOOK = os.environ.get("EDA_WEBHOOK", "")
INICIO = time.time()
ORDENES = {}
CONT = {"peticiones": 0, "errores": 0}
LOCK = threading.Lock()
CATALOGO = [
    {"id": "OFR-5G-EMP", "nombre": "Enlace 5G empresarial", "cnf": "upf", "vlan_desde": 300},
    {"id": "OFR-IOT", "nombre": "Conectividad IoT", "cnf": "amf", "vlan_desde": 400},
]
CLIENTES = [{"id": f"CLI-{i:05d}", "nombre": n} for i, n in enumerate(["Banco del Caribe", "Hospital San Juan", "Puerto de Ponce", "Retail Isla"], 1)]


def avisar_eda(orden):
    if not WEBHOOK:
        return
    try:
        cuerpo = json.dumps({"tipo": "orden", "evento": "creada", "orden": orden}).encode()
        urllib.request.urlopen(urllib.request.Request(WEBHOOK, cuerpo, {"Content-Type": "application/json"}), timeout=5)
        orden["historial"].append({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "estado": "enviada a Ansible (EDA)"})
    except Exception as e:  # noqa: BLE001
        orden["historial"].append({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "estado": f"EDA no respondió: {e}"})


class H(BaseHTTPRequestHandler):
    def _r(self, codigo, datos):
        b = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
        with LOCK:
            CONT["peticiones"] += 1
            CONT["errores"] += codigo >= 500

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        p = self.path.split("?")[0].rstrip("/")
        if p == "/health":
            sano = not (FALLA and time.time() - INICIO > 5)
            return self._r(200 if sano else 503, {"status": "UP" if sano else "DOWN", "rol": ROL, "version": VERSION})
        if p == "/version":
            return self._r(200, {"servicio": f"amdocs-{ROL}", "version": VERSION, "arriba_s": int(time.time() - INICIO)})
        if p == "/metrics":
            t = f"amdocs_peticiones_total {CONT['peticiones']}\namdocs_errores_total {CONT['errores']}\namdocs_ordenes {len(ORDENES)}\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            return self.wfile.write(t)
        if ROL == "ordering" and p == "/orders":
            est = (self.path.split("estado=")[1].split("&")[0] if "estado=" in self.path else "")
            return self._r(200, [o for o in ORDENES.values() if not est or o["estado"] == est])
        if ROL == "ordering" and p.startswith("/orders/"):
            o = ORDENES.get(p.split("/")[2])
            return self._r(200, o) if o else self._r(404, {"error": "orden no existe"})
        if ROL == "catalog" and p == "/offers":
            return self._r(200, CATALOGO)
        if ROL == "customer" and p == "/customers":
            return self._r(200, CLIENTES)
        return self._r(404, {"error": "ruta no existe"})

    def do_POST(self):  # noqa: N802
        if ROL == "ordering" and self.path.rstrip("/") == "/orders":
            d = self._cuerpo()
            oid = "ORD-" + uuid.uuid4().hex[:8].upper()
            o = {"id": oid, "estado": "nueva", "cliente": d.get("cliente", "CLI-00001"), "oferta": d.get("oferta", "OFR-5G-EMP"),
                 "servicio": {"cnf": d.get("cnf", "upf"), "nombre": d.get("nombre", "cnf-" + oid[-4:].lower()), "vlan": int(d.get("vlan", 300)),
                              "prefijo": d.get("prefijo", "10.60.10.0/24"), "ip_publica": d.get("ip_publica", "")},
                 "creada": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "historial": []}
            o["historial"].append({"t": o["creada"], "estado": "nueva"})
            ORDENES[oid] = o
            threading.Thread(target=avisar_eda, args=(o,), daemon=True).start()
            return self._r(201, o)
        return self._r(404, {"error": "ruta no existe"})

    def do_PATCH(self):  # noqa: N802
        p = self.path.rstrip("/")
        if ROL == "ordering" and p.startswith("/orders/"):
            o = ORDENES.get(p.split("/")[2])
            if not o:
                return self._r(404, {"error": "orden no existe"})
            d = self._cuerpo()
            o["estado"] = d.get("estado", o["estado"])
            o["historial"].append({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "estado": o["estado"], "detalle": d.get("detalle", "")})
            return self._r(200, o)
        return self._r(404, {"error": "ruta no existe"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
