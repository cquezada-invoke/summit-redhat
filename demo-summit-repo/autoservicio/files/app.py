#!/usr/bin/python3
# App de ejemplo del autoservicio: muestra qué se desplegó, para quién y dónde.
import html
import os
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

E = {k: html.escape(os.environ.get(k, "")) for k in ("APP", "AMBIENTE", "VERSION", "SOLICITANTE", "DESPLEGADO")}
COLOR = {"dev": "#2e90fa", "qa": "#f79009", "prod": "#12b76a"}.get(os.environ.get("AMBIENTE"), "#667085")
PAGINA = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{E['APP']} · {E['AMBIENTE']}</title>
<style>body{{margin:0;font-family:Arial,sans-serif;background:#f5f7fa;color:#101828}}
header{{background:#0b1f3a;color:#fff;padding:16px 24px;font-size:18px}}
main{{max-width:720px;margin:40px auto;padding:0 16px}}
.card{{background:#fff;border:1px solid #e4e7ec;border-radius:12px;padding:28px;border-top:8px solid {COLOR}}}
h1{{margin:0 0 12px;font-size:34px}}.chip{{display:inline-block;padding:4px 12px;border-radius:999px;color:#fff;background:{COLOR};font-weight:bold;margin-right:8px}}
.v{{background:#101828}}table{{margin-top:22px;font-size:15px}}td{{padding:6px 18px 6px 0}}td:first-child{{color:#667085}}
footer{{text-align:center;color:#667085;font-size:13px;margin-top:20px}}</style></head><body>
<header><b>Invoke</b> · Autoservicio de ambientes</header><main><div class="card">
<h1>{E['APP']}</h1><span class="chip">{E['AMBIENTE'].upper()}</span><span class="chip v">v{E['VERSION']}</span>
<table><tr><td>Solicitado por</td><td>{E['SOLICITANTE']}</td></tr>
<tr><td>Desplegado</td><td>{E['DESPLEGADO']}</td></tr>
<tr><td>Servidor</td><td>{html.escape(socket.gethostname())}</td></tr></table></div>
<footer>Creado sin tickets con Red Hat Ansible Automation Platform</footer></main></body></html>""".encode()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo = b"ok" if self.path.rstrip("/").endswith("salud") else PAGINA
        self.send_response(200)
        self.send_header("Content-Type", "text/plain" if cuerpo == b"ok" else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


HTTPServer(("0.0.0.0", int(os.environ["PUERTO"])), H).serve_forever()
