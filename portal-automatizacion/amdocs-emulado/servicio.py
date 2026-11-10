"""Amdocs emulado (demo Claro PR): microservicios BSS mínimos para mostrar automatizaciones con Ansible sin software Amdocs.
Un solo programa, tres roles (variable ROL), con la forma de las TM Forum Open APIs v4 que usan los BSS de telecomunicaciones:
  ordering → TMF622 Product Ordering   /tmf-api/productOrderingManagement/v4/productOrder  (GET, POST, GET/{id}, PATCH/{id})
  catalog  → TMF620 Product Catalog    /tmf-api/productCatalogManagement/v4/productOffering (GET, GET/{id})
  customer → TMF629 Customer           /tmf-api/customerManagement/v4/customer            (GET, GET/{id})
Es una emulación: solo los campos necesarios para la demo, sin validaciones de negocio ni persistencia (vive en memoria).
Compatibilidad: ordering mantiene /orders (formato simplificado) que usan el portal y los playbooks; ambas vistas son la
misma orden. Comunes: /health, /version, /metrics. VERSION: versión que informa. FALLA=si: la versión no queda sana (demo
de vuelta atrás de un release). EDA_WEBHOOK: URL a la que ordering avisa cada orden nueva (evento TMF ProductOrderCreateEvent)."""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROL = os.environ.get("ROL", "ordering")
VERSION = os.environ.get("VERSION", "1.0.0")
FALLA = os.environ.get("FALLA", "no") == "si"
WEBHOOK = os.environ.get("EDA_WEBHOOK", "")
INICIO = time.time()
ORDENES = {}   # id → orden en formato TMF622
CONT = {"peticiones": 0, "errores": 0}
LOCK = threading.Lock()

P622 = "/tmf-api/productOrderingManagement/v4/productOrder"
P620 = "/tmf-api/productCatalogManagement/v4/productOffering"
P629 = "/tmf-api/customerManagement/v4/customer"

OFERTAS = [   # TMF620 ProductOffering
    {"id": "OFR-5G-EMP", "href": f"{P620}/OFR-5G-EMP", "name": "Enlace 5G empresarial", "description": "Conectividad 5G dedicada con UPF propia (CNF de Nabstract)",
     "lifecycleStatus": "Active", "isBundle": False, "version": "1.0",
     "productSpecification": {"id": "PS-UPF", "name": "Servicio UPF", "@referredType": "ProductSpecification"},
     "prodSpecCharValueUse": [{"name": "cnf", "productSpecCharacteristicValue": [{"value": "upf", "isDefault": True}]},
                              {"name": "vlanDesde", "productSpecCharacteristicValue": [{"value": 300, "isDefault": True}]}],
     "@type": "ProductOffering"},
    {"id": "OFR-IOT", "href": f"{P620}/OFR-IOT", "name": "Conectividad IoT", "description": "Acceso IoT masivo con AMF dedicada (CNF de Nabstract)",
     "lifecycleStatus": "Active", "isBundle": False, "version": "1.0",
     "productSpecification": {"id": "PS-AMF", "name": "Servicio AMF", "@referredType": "ProductSpecification"},
     "prodSpecCharValueUse": [{"name": "cnf", "productSpecCharacteristicValue": [{"value": "amf", "isDefault": True}]},
                              {"name": "vlanDesde", "productSpecCharacteristicValue": [{"value": 400, "isDefault": True}]}],
     "@type": "ProductOffering"},
]
CLIENTES = [   # TMF629 Customer
    {"id": f"CLI-{i:05d}", "href": f"{P629}/CLI-{i:05d}", "name": n, "status": "Approved",
     "engagedParty": {"id": f"ORG-{i:05d}", "name": n, "@referredType": "Organization"}, "@type": "Customer"}
    for i, n in enumerate(["Banco del Caribe", "Hospital San Juan", "Puerto de Ponce", "Retail Isla"], 1)]

# estados TMF622 ↔ estados que usan el portal y los playbooks
A_TMF = {"nueva": "acknowledged", "en aprobación": "pending", "en curso": "inProgress", "completada": "completed",
         "fallida": "failed", "rechazada": "rejected", "cancelada": "cancelled"}
DE_TMF = {v: k for k, v in A_TMF.items()}


def ahora():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def caract(o, nombre, defecto=""):
    for c in o["productOrderItem"][0]["product"].get("productCharacteristic", []):
        if c["name"] == nombre:
            return c["value"]
    return defecto


def simple(o):
    """Vista /orders (formato simplificado que usan el portal y los playbooks) de una orden TMF622."""
    cli = next((p for p in o.get("relatedParty", []) if p.get("role") == "Customer"), {})
    oferta = o["productOrderItem"][0]["productOffering"]
    return {"id": o["id"], "estado": DE_TMF.get(o["state"], o["state"]), "cliente": cli.get("id", ""), "cliente_nombre": cli.get("name", ""),
            "oferta": oferta["id"], "oferta_nombre": oferta.get("name", ""),
            "servicio": {"cnf": caract(o, "cnf", "upf"), "nombre": caract(o, "nombreServicio"), "vlan": int(caract(o, "vlan", 300)),
                         "prefijo": caract(o, "prefijo"), "ip_publica": caract(o, "ipPublica")},
            "creada": o["orderDate"],
            "historial": [{"t": n["date"], "estado": n.get("estado", ""), "detalle": n.get("detalle", "")} for n in o.get("note", [])]}


def nueva_orden(cliente, oferta_id, cnf, nombre, vlan, prefijo, ip_publica="", externo=""):
    oid = "ORD-" + uuid.uuid4().hex[:8].upper()
    cli = next((c for c in CLIENTES if c["id"] == cliente), {"id": cliente, "name": cliente})
    of = next((x for x in OFERTAS if x["id"] == oferta_id), {"id": oferta_id, "name": oferta_id})
    o = {"id": oid, "href": f"{P622}/{oid}", "externalId": externo or "", "category": "B2B",
         "description": f"Alta de {of['name']} para {cli['name']}", "priority": "4", "state": "acknowledged",
         "orderDate": ahora(), "requestedStartDate": ahora(),
         "relatedParty": [{"id": cli["id"], "name": cli["name"], "role": "Customer", "@referredType": "Customer"}],
         "productOrderItem": [{"id": "1", "quantity": 1, "action": "add", "state": "acknowledged",
                               "productOffering": {"id": of["id"], "name": of["name"], "href": f"{P620}/{of['id']}", "@referredType": "ProductOffering"},
                               "product": {"@type": "Product", "productCharacteristic": [
                                   {"name": "cnf", "valueType": "string", "value": cnf},
                                   {"name": "nombreServicio", "valueType": "string", "value": nombre or ("cnf-" + oid[-4:].lower())},
                                   {"name": "vlan", "valueType": "integer", "value": int(vlan)},
                                   {"name": "prefijo", "valueType": "string", "value": prefijo},
                                   {"name": "ipPublica", "valueType": "string", "value": ip_publica}]}}],
         "note": [{"date": ahora(), "author": "Amdocs (emulado)", "text": "Orden recibida", "estado": "nueva"}],
         "@type": "ProductOrder"}
    ORDENES[oid] = o
    threading.Thread(target=avisar_eda, args=(o,), daemon=True).start()
    return o


def cambiar_estado(o, estado, detalle="", autor="Ansible Automation Platform"):
    """estado en el vocabulario del portal (nueva, en aprobación, completada, ...) o en TMF622 (acknowledged, ...)."""
    tmf = A_TMF.get(estado, estado)
    o["state"] = o["productOrderItem"][0]["state"] = tmf
    if tmf in ("completed", "failed", "cancelled", "rejected"):
        o["completionDate"] = ahora()
    o["note"].append({"date": ahora(), "author": autor, "text": detalle or DE_TMF.get(tmf, tmf), "estado": DE_TMF.get(tmf, tmf), "detalle": detalle})


def avisar_eda(o):
    if not WEBHOOK:
        return
    try:
        cuerpo = json.dumps({"tipo": "orden", "evento": "creada", "eventType": "ProductOrderCreateEvent",
                             "event": {"productOrder": o}, "orden": simple(o)}).encode()
        urllib.request.urlopen(urllib.request.Request(WEBHOOK, cuerpo, {"Content-Type": "application/json"}), timeout=5)
        o["note"].append({"date": ahora(), "author": "Amdocs (emulado)", "text": "Evento ProductOrderCreateEvent enviado a Ansible (EDA)", "estado": "enviada a Ansible (EDA)"})
    except Exception as e:  # noqa: BLE001
        o["note"].append({"date": ahora(), "author": "Amdocs (emulado)", "text": f"EDA no respondió: {e}", "estado": "EDA no respondió"})


PANTALLA = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pantalla.html"), encoding="utf-8").read()


class H(BaseHTTPRequestHandler):
    def _r(self, codigo, datos, cab=None):
        b = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (cab or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)
        with LOCK:
            CONT["peticiones"] += 1
            CONT["errores"] += codigo >= 500

    def _err(self, codigo, razon):   # forma de error de las TMF Open APIs
        return self._r(codigo, {"code": str(codigo), "reason": razon, "@type": "Error"})

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, *a):
        pass

    def _lista(self, items):
        return self._r(200, items, {"X-Total-Count": str(len(items)), "X-Result-Count": str(len(items))})

    def do_GET(self):  # noqa: N802
        p = self.path.split("?")[0].rstrip("/")
        q = dict(x.split("=", 1) for x in self.path.split("?", 1)[1].split("&") if "=" in x) if "?" in self.path else {}
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
        if ROL == "ordering" and p in ("", "/ui"):
            b = PANTALLA.replace("__OFERTAS__", json.dumps(OFERTAS, ensure_ascii=False)).replace("__CLIENTES__", json.dumps(CLIENTES, ensure_ascii=False)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if ROL == "ordering":
            if p == P622:
                return self._lista([o for o in ORDENES.values() if "state" not in q or o["state"] == q["state"]])
            if p.startswith(P622 + "/"):
                o = ORDENES.get(p.rsplit("/", 1)[1])
                return self._r(200, o) if o else self._err(404, "productOrder no existe")
            if p == "/orders":
                est = urllib.parse.unquote(q.get("estado", ""))
                return self._r(200, [simple(o) for o in ORDENES.values() if not est or simple(o)["estado"] == est])
            if p.startswith("/orders/"):
                o = ORDENES.get(p.split("/")[2])
                return self._r(200, simple(o)) if o else self._r(404, {"error": "orden no existe"})
        if ROL == "catalog":
            if p in (P620, "/offers"):
                return self._lista(OFERTAS)
            if p.startswith(P620 + "/"):
                o = next((x for x in OFERTAS if x["id"] == p.rsplit("/", 1)[1]), None)
                return self._r(200, o) if o else self._err(404, "productOffering no existe")
        if ROL == "customer":
            if p in (P629, "/customers"):
                return self._lista(CLIENTES)
            if p.startswith(P629 + "/"):
                c = next((x for x in CLIENTES if x["id"] == p.rsplit("/", 1)[1]), None)
                return self._r(200, c) if c else self._err(404, "customer no existe")
        return self._err(404, "ruta no existe")

    def do_POST(self):  # noqa: N802
        p = self.path.split("?")[0].rstrip("/")
        if ROL == "ordering" and p == P622:   # TMF622: ProductOrder_Create
            d = self._cuerpo()
            try:
                it = d["productOrderItem"][0]
                ch = {c["name"]: c["value"] for c in (it.get("product") or {}).get("productCharacteristic", [])}
                cli = next((x["id"] for x in d.get("relatedParty", []) if x.get("role") == "Customer"), "CLI-00001")
                o = nueva_orden(cli, it["productOffering"]["id"], ch.get("cnf", "upf"), ch.get("nombreServicio", ""), ch.get("vlan", 300),
                                ch.get("prefijo", "10.60.10.0/24"), ch.get("ipPublica", ""), d.get("externalId", ""))
            except (KeyError, IndexError, TypeError, ValueError) as e:
                return self._err(400, f"productOrder inválido: {e}")
            return self._r(201, o)
        if ROL == "ordering" and p == "/orders":   # forma simplificada (portal)
            d = self._cuerpo()
            o = nueva_orden(d.get("cliente", "CLI-00001"), d.get("oferta", "OFR-5G-EMP"), d.get("cnf", "upf"), d.get("nombre", ""),
                            d.get("vlan", 300), d.get("prefijo", "10.60.10.0/24"), d.get("ip_publica", ""))
            return self._r(201, simple(o))
        return self._err(404, "ruta no existe")

    def do_PATCH(self):  # noqa: N802
        p = self.path.split("?")[0].rstrip("/")
        if ROL == "ordering" and p.startswith(P622 + "/"):   # TMF622: ProductOrder_Update (state)
            o = ORDENES.get(p.rsplit("/", 1)[1])
            if not o:
                return self._err(404, "productOrder no existe")
            d = self._cuerpo()
            if d.get("state"):
                cambiar_estado(o, d["state"], (d.get("note") or [{}])[0].get("text", ""), "API TMF622")
            return self._r(200, o)
        if ROL == "ordering" and p.startswith("/orders/"):
            o = ORDENES.get(p.split("/")[2])
            if not o:
                return self._r(404, {"error": "orden no existe"})
            d = self._cuerpo()
            cambiar_estado(o, d.get("estado", DE_TMF.get(o["state"], o["state"])), d.get("detalle", ""))
            return self._r(200, simple(o))
        return self._err(404, "ruta no existe")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
