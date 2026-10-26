#!/usr/bin/python3
# Exportador mínimo de Artemis para Prometheus: lee por Jolokia los mensajes y consumidores de cada cola.
# Métricas: artemis_cola_mensajes{cola}, artemis_cola_consumidores{cola}. Credenciales en /etc/summit-monitoreo/artemis.env
import base64, json, os, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
U, P = os.environ["ARTEMIS_USER"], os.environ["ARTEMIS_PASSWORD"]
URL = os.environ.get("ARTEMIS_URL", "http://192.168.122.14:8161")
MB = 'org.apache.activemq.artemis:broker=*,component=addresses,address=*,subcomponent=queues,routing-type=*,queue=*'

def leer():
    req = urllib.request.Request(f"{URL}/console/jolokia/read/{MB}/MessageCount,ConsumerCount",
                                 headers={"Authorization": "Basic " + base64.b64encode(f"{U}:{P}".encode()).decode(),
                                          "Origin": URL})
    v = json.load(urllib.request.urlopen(req, timeout=5)).get("value", {})
    out = ["# TYPE artemis_cola_mensajes gauge", "# TYPE artemis_cola_consumidores gauge"]
    for k, x in v.items():
        cola = k.split('queue="', 1)[1].split('"', 1)[0]
        if cola.startswith("$"):
            continue
        out.append(f'artemis_cola_mensajes{{cola="{cola}"}} {x["MessageCount"]}')
        out.append(f'artemis_cola_consumidores{{cola="{cola}"}} {x["ConsumerCount"]}')
    return "\n".join(out) + "\nartemis_arriba 1\n"

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            b = leer().encode(); c = 200
        except Exception as e:
            b = f"artemis_arriba 0\n# error {e}\n".encode(); c = 200
        self.send_response(c); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", 9410), H).serve_forever()
