"""Enlaces de un solo uso para que un usuario defina su clave (y luego active su doble factor). Se guarda solo la huella."""
import hashlib
import secrets
import time

from .bd import ej, q1


def iniciar():
    ej("CREATE TABLE IF NOT EXISTS activaciones (id INTEGER PRIMARY KEY, usuario TEXT NOT NULL, token TEXT NOT NULL, vence REAL NOT NULL, usado INTEGER DEFAULT 0)")


def nuevo(usuario, horas=72):
    iniciar()
    if not q1("SELECT id FROM usuarios WHERE usuario=?", usuario):
        raise ValueError("usuario desconocido")
    tok = secrets.token_urlsafe(24)
    ej("UPDATE activaciones SET usado=1 WHERE usuario=?", usuario)
    ej("INSERT INTO activaciones (usuario, token, vence) VALUES (?,?,?)", usuario, hashlib.sha256(tok.encode()).hexdigest(), time.time() + horas * 3600)
    return tok
