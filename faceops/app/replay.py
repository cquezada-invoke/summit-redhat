"""Maqueta: grabar y reproducir las respuestas de los servicios externos (AAP, Gitea, Vault…) para navegarla completa
sin conexión.
  FACEOPS_GRABAR=<archivo.json>      cada GET exitoso a un servicio externo se guarda (sin encabezados de autenticación)
  FACEOPS_REPRODUCIR=<archivo.json>  los GET se responden desde el archivo; lo demás falla al instante (sin esperas de red)
Solo se usa en la maqueta; la consola real no define ninguna de las dos variables."""
import base64
import json
import os
import threading

import requests

_lock = threading.Lock()
_cache = {}
_ruta = None


def _clave(metodo, url):
    return f"{metodo} {url}"


def _guardar():
    tmp = _ruta + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_cache, f)
    os.replace(tmp, _ruta)


def activar():
    global _ruta
    grabar, reproducir = os.environ.get("FACEOPS_GRABAR"), os.environ.get("FACEOPS_REPRODUCIR")
    if not (grabar or reproducir):
        return
    _ruta = grabar or reproducir
    if os.path.exists(_ruta):
        try:
            _cache.update(json.load(open(_ruta)))
        except ValueError:
            pass
    original = requests.Session.send

    def send(self, req, **kw):
        k = _clave(req.method, req.url)
        if reproducir:
            x = _cache.get(k)
            if req.method != "GET" or not x:
                raise requests.ConnectionError(f"maqueta sin conexión: {req.method} {req.url}")
            r = requests.Response()
            r.status_code, r.url, r.request = x["s"], req.url, req
            r._content = base64.b64decode(x["b"])
            r.headers["Content-Type"] = x.get("t", "application/json")
            r.encoding = "utf-8"
            return r
        r = original(self, req, **kw)
        privado = ":8200" in req.url or "/v1/sys" in req.url or "/v1/faceops" in req.url or "credential" in req.url or "token" in req.url.lower()
        if req.method == "GET" and not privado and r.status_code < 400 and len(r.content) <= 2_000_000:
            with _lock:
                _cache[k] = {"s": r.status_code, "t": r.headers.get("Content-Type", ""), "b": base64.b64encode(r.content).decode()}
                _guardar()
        return r

    requests.Session.send = send
