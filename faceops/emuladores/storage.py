#!/usr/bin/python3
"""Invoke SAN emulada: un arreglo de almacenamiento para el laboratorio (pools, volúmenes, asignación a servidores,
snapshots y métricas) con API REST y una consola web. Los volúmenes son archivos dispersos reales en /srv/storage, así
la capacidad usada se ve en el disco. Solo librería estándar de Python. Puerto 8080.
API (JSON): GET /api/v1/sistema · /pools · /volumenes · /hosts · /metricas
            POST /api/v1/volumenes {nombre, gb, pool} · POST /api/v1/volumenes/<nombre>/ampliar {gb}
            POST /api/v1/volumenes/<nombre>/asignar {host, protocolo} · POST /api/v1/volumenes/<nombre>/snapshot
            DELETE /api/v1/volumenes/<nombre>
Escritura con encabezado «Authorization: Bearer <token>» (/etc/faceops-storage/token)."""
import json, os, random, re, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIR = os.environ.get("STORAGE_DIR", "/srv/storage")
ESTADO = os.path.join(DIR, "estado.json")
TOKEN = open("/etc/faceops-storage/token").read().strip() if os.path.exists("/etc/faceops-storage/token") else ""
CANDADO = threading.Lock()
ahora = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")


def cargar():
    if os.path.exists(ESTADO):
        return json.load(open(ESTADO))
    return {"sistema": {"modelo": "Invoke SAN emulada", "serie": "INV-SAN-0001", "firmware": "2.4.1", "controladoras": 2},
            "pools": {"oro": {"tipo": "SSD", "capacidad_gb": 2048, "raid": "RAID-10"}, "plata": {"tipo": "SAS", "capacidad_gb": 8192, "raid": "RAID-6"}},
            "volumenes": {}, "hosts": {}, "eventos": []}


def guardar(e):
    tmp = ESTADO + ".tmp"
    json.dump(e, open(tmp, "w"), indent=1, ensure_ascii=False)
    os.replace(tmp, ESTADO)


def usado(e, pool):
    return sum(v["gb"] for v in e["volumenes"].values() if v["pool"] == pool)


def evento(e, t):
    e["eventos"] = ([{"cuando": ahora(), "texto": t}] + e["eventos"])[:50]


class H(BaseHTTPRequestHandler):
    def _r(self, code, d, tipo="application/json"):
        b = d.encode() if isinstance(d, str) else json.dumps(d, ensure_ascii=False, indent=1).encode()
        self.send_response(code); self.send_header("Content-Type", tipo + "; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        e = cargar()
        p = self.path.split("?")[0].rstrip("/")
        if p in ("", "/"):
            return self._r(200, PAGINA(e), "text/html")
        if p == "/api/v1/sistema":
            cap = sum(x["capacidad_gb"] for x in e["pools"].values())
            return self._r(200, dict(e["sistema"], capacidad_gb=cap, asignado_gb=sum(v["gb"] for v in e["volumenes"].values()), estado="óptimo"))
        if p == "/api/v1/pools":
            return self._r(200, {k: dict(v, usado_gb=usado(e, k)) for k, v in e["pools"].items()})
        if p == "/api/v1/volumenes":
            return self._r(200, e["volumenes"])
        if p == "/api/v1/hosts":
            return self._r(200, e["hosts"])
        if p == "/api/v1/metricas":
            n = len(e["volumenes"])
            return self._r(200, {"iops": random.randint(800, 1500) * max(1, n), "latencia_ms": round(random.uniform(0.4, 1.8), 2),
                                 "throughput_mbps": random.randint(120, 480), "volumenes": n})
        if p == "/api/v1/eventos":
            return self._r(200, e["eventos"])
        return self._r(404, {"error": "no existe"})

    def _cuerpo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _auth(self):
        if TOKEN and self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._r(401, {"error": "token inválido"}); return False
        return True

    def do_POST(self):
        if not self._auth():
            return
        d, p = self._cuerpo(), self.path.rstrip("/")
        with CANDADO:
            e = cargar()
            if p == "/api/v1/volumenes":
                n, gb, pool = str(d.get("nombre", "")), int(d.get("gb", 0)), d.get("pool", "plata")
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", n) or gb <= 0 or pool not in e["pools"]:
                    return self._r(400, {"error": "nombre (minúsculas, números, guiones), gb > 0 y pool válido (" + ", ".join(e["pools"]) + ")"})
                if n in e["volumenes"]:
                    return self._r(200, dict(e["volumenes"][n], ya_existia=True))
                if usado(e, pool) + gb > e["pools"][pool]["capacidad_gb"]:
                    return self._r(409, {"error": f"sin capacidad en el pool {pool}"})
                os.makedirs(os.path.join(DIR, "volumenes"), exist_ok=True)
                with open(os.path.join(DIR, "volumenes", n + ".img"), "wb") as f:
                    f.truncate(gb * 1024 ** 3)   # archivo disperso: ocupa solo lo escrito
                e["volumenes"][n] = {"nombre": n, "gb": gb, "pool": pool, "wwn": "6001405" + os.urandom(6).hex(), "creado": ahora(),
                                     "asignado_a": [], "snapshots": []}
                evento(e, f"volumen {n} creado ({gb} GB en {pool})")
                guardar(e)
                return self._r(201, e["volumenes"][n])
            m = re.fullmatch(r"/api/v1/volumenes/([a-z0-9-]+)/(ampliar|asignar|snapshot)", p)
            if not m or m.group(1) not in e["volumenes"]:
                return self._r(404, {"error": "volumen no existe"})
            v, acc = e["volumenes"][m.group(1)], m.group(2)
            if acc == "ampliar":
                gb = int(d.get("gb", 0))
                if gb <= v["gb"]:
                    return self._r(400, {"error": "el tamaño nuevo debe ser mayor"})
                if usado(e, v["pool"]) - v["gb"] + gb > e["pools"][v["pool"]]["capacidad_gb"]:
                    return self._r(409, {"error": "sin capacidad en el pool"})
                os.truncate(os.path.join(DIR, "volumenes", v["nombre"] + ".img"), gb * 1024 ** 3)
                evento(e, f"volumen {v['nombre']} ampliado de {v['gb']} a {gb} GB"); v["gb"] = gb
            elif acc == "asignar":
                h, proto = str(d.get("host", "")), d.get("protocolo", "iscsi")
                if not h:
                    return self._r(400, {"error": "falta host"})
                e["hosts"].setdefault(h, {"nombre": h, "iqn": f"iqn.2026-10.cl.invoke:{h}", "protocolo": proto})
                if h not in v["asignado_a"]:
                    v["asignado_a"].append(h)
                evento(e, f"volumen {v['nombre']} asignado a {h} ({proto})")
            else:
                s = {"nombre": f"{v['nombre']}-snap-{len(v['snapshots']) + 1}", "creado": ahora()}
                v["snapshots"].append(s); evento(e, f"snapshot {s['nombre']}")
            guardar(e)
            return self._r(200, v)

    def do_DELETE(self):
        if not self._auth():
            return
        m = re.fullmatch(r"/api/v1/volumenes/([a-z0-9-]+)", self.path.rstrip("/"))
        with CANDADO:
            e = cargar()
            if not m or m.group(1) not in e["volumenes"]:
                return self._r(404, {"error": "volumen no existe"})
            v = e["volumenes"].pop(m.group(1))
            try:
                os.remove(os.path.join(DIR, "volumenes", v["nombre"] + ".img"))
            except OSError:
                pass
            evento(e, f"volumen {v['nombre']} eliminado"); guardar(e)
            return self._r(200, {"eliminado": v["nombre"]})


def PAGINA(e):
    filas = "".join(f"<tr><td>{v['nombre']}</td><td>{v['pool']}</td><td style='text-align:right'>{v['gb']} GB</td><td>{', '.join(v['asignado_a']) or '—'}</td>"
                    f"<td>{len(v['snapshots'])}</td><td style='font-family:monospace;font-size:12px'>{v['wwn']}</td></tr>" for v in e["volumenes"].values())
    pools = "".join(f"<div class='p'><b>{k}</b> · {v['tipo']} {v['raid']}<div class='b'><i style='width:{min(100, usado(e, k) / v['capacidad_gb'] * 100):.0f}%'></i></div>"
                    f"{usado(e, k)} / {v['capacidad_gb']} GB</div>" for k, v in e["pools"].items())
    ev = "".join(f"<li>{x['cuando'][:16].replace('T', ' ')} · {x['texto']}</li>" for x in e["eventos"][:12])
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{e['sistema']['modelo']}</title><meta name="viewport" content="width=device-width">
<style>body{{font:14px system-ui,sans-serif;margin:0;background:#f0f0f0;color:#151515}}header{{background:#151515;color:#fff;padding:12px 20px}}
main{{padding:20px;max-width:1100px}}.c{{background:#fff;border:1px solid #d2d2d2;padding:16px;margin-bottom:14px}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left}}.p{{display:inline-block;width:46%;margin-right:3%}}.b{{height:8px;background:#eee;margin:6px 0}}.b i{{display:block;height:8px;background:#0066cc}}</style></head>
<body><header><b>{e['sistema']['modelo']}</b> · serie {e['sistema']['serie']} · firmware {e['sistema']['firmware']} · estado óptimo</header><main>
<div class="c"><h3>Pools</h3>{pools}</div><div class="c"><h3>Volúmenes ({len(e['volumenes'])})</h3><table><tr><th>Volumen</th><th>Pool</th><th>Tamaño</th><th>Asignado a</th><th>Snapshots</th><th>WWN</th></tr>{filas or '<tr><td colspan=6>Sin volúmenes</td></tr>'}</table></div>
<div class="c"><h3>Eventos</h3><ul>{ev or '<li>—</li>'}</ul></div></main></body></html>"""


if __name__ == "__main__":
    os.makedirs(DIR, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("STORAGE_PUERTO", "8080"))), H).serve_forever()
