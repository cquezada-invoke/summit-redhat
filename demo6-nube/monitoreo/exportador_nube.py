#!/usr/bin/python3
# Exportador de la nube (Demo 6) para Prometheus. Corre en la VM del Summit (:9420).
# Los playbooks de la organización "Nube" de AAP le informan lo que hicieron (POST JSON):
#   /vm        {accion: alta|baja, nombre, ip}     → VMs creadas por Ansible; escribe el file_sd de Prometheus
#   /huerfanos {total, usd_mes, limpiables, por_tipo}
#   /firewall  {corregidas: [...], ajenas: [...]}  → responde {nuevas: [...]} (ajenas no avisadas antes)
#   /estado    {conectada: 0|1}
# GET /metrics para Prometheus. Estado persistente en ESTADO (JSON). Sin dependencias externas.
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PUERTO = int(os.environ.get("PUERTO", "9420"))
ESTADO = os.environ.get("ESTADO", os.path.expanduser("~/.local/share/summit-nube/estado.json"))
FILE_SD = os.environ.get("FILE_SD", "/opt/summit-monitoreo/prometheus/nube/vms.json")
_L = threading.Lock()


def cargar():
    try:
        return json.load(open(ESTADO))
    except Exception:
        return {"vms": {}, "altas": 0, "bajas": 0, "conectada": 0, "huerfanos": {}, "firewall": {},
                "fw_corregidas_total": 0, "fw_avisadas": []}


E = cargar()


def guardar():
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    json.dump(E, open(ESTADO + ".tmp", "w"))
    os.replace(ESTADO + ".tmp", ESTADO)
    try:
        sd = [{"targets": [f"{v['ip']}:9100"], "labels": {"nombre": n, "rol": "VM en GCP (Demo 6)", "sistema": "linux", "nube": "gcp"}}
              for n, v in sorted(E["vms"].items()) if v.get("ip")]
        os.makedirs(os.path.dirname(FILE_SD), exist_ok=True)
        json.dump(sd, open(FILE_SD + ".tmp", "w"))
        os.replace(FILE_SD + ".tmp", FILE_SD)
    except Exception as e:
        print("file_sd:", e, flush=True)


def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def metricas():
    l = ["# HELP nube_conectada 1 si AAP tiene la credencial de GCP y pudo leer el proyecto",
         "# TYPE nube_conectada gauge", f"nube_conectada {E['conectada']}",
         "# TYPE nube_vms_activas gauge", f"nube_vms_activas {len(E['vms'])}",
         "# TYPE nube_vms_creadas_total counter", f"nube_vms_creadas_total {E['altas']}",
         "# TYPE nube_vms_bajas_total counter", f"nube_vms_bajas_total {E['bajas']}",
         "# TYPE nube_vm_info gauge"]
    l += [f'nube_vm_info{{nombre="{esc(n)}",ip="{esc(v.get("ip", ""))}"}} {v.get("desde", 0)}' for n, v in sorted(E["vms"].items())]
    h = E.get("huerfanos") or {}
    l += ["# HELP nube_huerfanos recursos sin uso en el último reporte", "# TYPE nube_huerfanos gauge",
          f"nube_huerfanos {h.get('total', 0)}",
          "# TYPE nube_huerfanos_usd_mes gauge", f"nube_huerfanos_usd_mes {h.get('usd_mes', 0)}",
          "# TYPE nube_huerfanos_limpiables gauge", f"nube_huerfanos_limpiables {h.get('limpiables', 0)}",
          "# TYPE nube_huerfanos_tipo gauge"]
    l += [f'nube_huerfanos_tipo{{tipo="{esc(t)}"}} {c}' for t, c in (h.get("por_tipo") or {}).items()]
    l += ["# TYPE nube_huerfanos_reporte_timestamp gauge", f"nube_huerfanos_reporte_timestamp {h.get('ts', 0)}"]
    f = E.get("firewall") or {}
    l += ["# HELP nube_firewall_peligrosas reglas abiertas a internet en puertos sensibles (última revisión)",
          "# TYPE nube_firewall_peligrosas gauge",
          f'nube_firewall_peligrosas{{tipo="corregida"}} {len(f.get("corregidas", []))}',
          f'nube_firewall_peligrosas{{tipo="ajena"}} {len(f.get("ajenas", []))}',
          "# TYPE nube_firewall_corregidas_total counter", f"nube_firewall_corregidas_total {E['fw_corregidas_total']}",
          "# TYPE nube_firewall_revision_timestamp gauge", f"nube_firewall_revision_timestamp {f.get('ts', 0)}"]
    return "\n".join(l) + "\n"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _resp(self, code, cuerpo, tipo="application/json"):
        b = cuerpo.encode()
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/metrics"):
            with _L:
                return self._resp(200, metricas(), "text/plain; version=0.0.4")
        self._resp(200, json.dumps(E, ensure_ascii=False))

    def do_POST(self):
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except Exception:
            return self._resp(400, '{"error":"json"}')
        r = {"ok": True}
        with _L:
            if self.path == "/vm":
                n = d.get("nombre", "")
                if d.get("accion") == "alta" and n:
                    E["vms"][n] = {"ip": d.get("ip", ""), "desde": int(time.time())}
                    E["altas"] += 1
                elif d.get("accion") == "baja" and n:
                    if E["vms"].pop(n, None) is not None:
                        E["bajas"] += 1
                E["conectada"] = 1
            elif self.path == "/huerfanos":
                E["huerfanos"] = {"total": int(d.get("total", 0)), "usd_mes": float(d.get("usd_mes", 0)),
                                  "limpiables": int(d.get("limpiables", 0)), "por_tipo": d.get("por_tipo") or {},
                                  "ts": int(time.time())}
                E["conectada"] = 1
            elif self.path == "/firewall":
                corr, aj = list(d.get("corregidas") or []), list(d.get("ajenas") or [])
                r["nuevas"] = [a for a in aj if a not in E["fw_avisadas"]]
                E["fw_avisadas"] = aj  # si una ajena se arregla y vuelve a abrirse, se avisa de nuevo
                E["fw_corregidas_total"] += len(corr)
                E["firewall"] = {"corregidas": corr, "ajenas": aj, "ts": int(time.time())}
                E["conectada"] = 1
            elif self.path == "/estado":
                E["conectada"] = int(d.get("conectada", 0))
            else:
                return self._resp(404, '{"error":"ruta"}')
            guardar()
        print(time.strftime("%H:%M:%S"), self.path, json.dumps(d, ensure_ascii=False)[:200], flush=True)
        self._resp(200, json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    with _L:
        guardar()
    ThreadingHTTPServer(("0.0.0.0", PUERTO), H).serve_forever()
