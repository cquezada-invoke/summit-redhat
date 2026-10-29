"""Datos iniciales: administrador, cliente de demostración "Invoke Lab" y gobierno base (segmentos, skills, políticas).
Se ejecuta en cada arranque, pero solo crea lo que falta. Las claves iniciales quedan en /var/lib/faceops/credenciales-iniciales.txt."""
import json
import os
import secrets

import yaml
from werkzeug.security import generate_password_hash

from .bd import RUTA, ahora, ej, q, q1

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datos")


def _clave():
    return secrets.token_urlsafe(9) + "-A1"


def _anotar(linea):
    ruta = os.path.join(os.path.dirname(RUTA), "credenciales-iniciales.txt")
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.write(fd, (linea + "\n").encode())
    os.close(fd)


def crear_usuario(usuario, nombre, es_admin=0, correo=""):
    u = q1("SELECT id FROM usuarios WHERE usuario=?", usuario)
    if u:
        return u["id"], None
    clave = _clave()
    uid = ej("INSERT INTO usuarios(usuario, nombre, correo, clave_hash, es_admin, creado) VALUES (?,?,?,?,?,?)",
             usuario, nombre, correo, generate_password_hash(clave), es_admin, ahora())
    _anotar(f"{usuario}={clave}")
    return uid, clave


def cargar_gobierno(cid, d, reemplazar=False):
    if reemplazar:
        for t in ("segmentos", "skills", "politicas"):
            if t in d:
                ej(f"DELETE FROM {t} WHERE cliente_id=?", cid)
    for s in d.get("segmentos", []):
        ej("INSERT OR IGNORE INTO segmentos(cliente_id, nombre, tipo, regla, descripcion, orden) VALUES (?,?,?,?,?,?)",
           cid, s["nombre"], s.get("tipo", "negocio"), json.dumps(s.get("regla", {}), ensure_ascii=False),
           s.get("descripcion", ""), s.get("orden", 0))
    for s in d.get("skills", []):
        ej("INSERT OR IGNORE INTO skills(cliente_id, nombre, descripcion, regla) VALUES (?,?,?,?)",
           cid, s["nombre"], s.get("descripcion", ""), json.dumps(s.get("regla", {}), ensure_ascii=False))
    for p in d.get("politicas", []):
        ej("INSERT OR IGNORE INTO politicas(cliente_id, nombre, descripcion, severidad, aplica, controles, activa) VALUES (?,?,?,?,?,?,?)",
           cid, p["nombre"], p.get("descripcion", ""), p.get("severidad", "media"),
           json.dumps(p.get("aplica", {}), ensure_ascii=False), json.dumps(p.get("controles", []), ensure_ascii=False),
           int(p.get("activa", 1)))


def exportar_gobierno(cid):
    sal = lambda v: json.loads(v) if v else {}
    return {
        "segmentos": [{"nombre": s["nombre"], "tipo": s["tipo"], "orden": s["orden"], "descripcion": s["descripcion"],
                       "regla": sal(s["regla"])} for s in q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY tipo, orden, nombre", cid)],
        "skills": [{"nombre": s["nombre"], "descripcion": s["descripcion"], "regla": sal(s["regla"])}
                   for s in q("SELECT * FROM skills WHERE cliente_id=? ORDER BY nombre", cid)],
        "politicas": [{"nombre": p["nombre"], "descripcion": p["descripcion"], "severidad": p["severidad"], "activa": p["activa"],
                       "aplica": sal(p["aplica"]), "controles": json.loads(p["controles"] or "[]")}
                      for p in q("SELECT * FROM politicas WHERE cliente_id=? ORDER BY nombre", cid)],
    }


def gobierno_base(cid):
    cargar_gobierno(cid, yaml.safe_load(open(os.path.join(DATOS, "gobierno-base.yml"))))


def sembrar():
    admin = os.environ.get("FACEOPS_ADMIN", "cquezada")
    crear_usuario(admin, os.environ.get("FACEOPS_ADMIN_NOMBRE", "Cristian Quezada"), es_admin=1, correo="cquezada@invoke.cl")
    lab = yaml.safe_load(open(os.path.join(DATOS, "invoke-lab.yml")))
    c = lab["cliente"]
    if not q1("SELECT id FROM clientes WHERE codigo=?", c["codigo"]):
        cid = ej("INSERT INTO clientes(codigo, nombre, descripcion, aap_url, aap_token, moneda, costo_hora, clasificacion, recoleccion, creado) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?)", c["codigo"], c["nombre"], c["descripcion"],
                 os.environ.get("FACEOPS_AAP_URL", c["aap_url"]), os.environ.get("FACEOPS_AAP_TOKEN", ""), c["moneda"], c["costo_hora"],
                 json.dumps(lab["clasificacion"], ensure_ascii=False), json.dumps(lab["recoleccion"], ensure_ascii=False), ahora())
        gobierno_base(cid)
        cargar_gobierno(cid, {"segmentos": lab["segmentos"]})
        for u in lab["usuarios"]:
            uid, _ = crear_usuario(u["usuario"], u["nombre"])
            for p in u["perfiles"]:
                ej("INSERT OR IGNORE INTO perfiles(usuario_id, cliente_id, segmentos, ambientes, modo, aprobador) VALUES (?,?,?,?,?,?)",
                   uid, cid, json.dumps(p["segmentos"], ensure_ascii=False), json.dumps(p.get("ambientes", ["*"])), p["modo"], p["aprobador"])
    elif os.environ.get("FACEOPS_AAP_TOKEN"):
        # Token rotado desde el despliegue
        ej("UPDATE clientes SET aap_token=? WHERE codigo=? AND (aap_token='' OR aap_token IS NULL)", os.environ["FACEOPS_AAP_TOKEN"], c["codigo"])
    migrar_segmentos_negocio(lab)
    migrar_skills_sin_so()


def migrar_segmentos_negocio(lab):
    """28/10: los segmentos pasan a ser solo de negocio; infraestructura (ubicación) y ambiente son columnas del equipo."""
    from .bd import ajuste
    if ajuste("migracion_segmentos_negocio"):
        return
    c = q1("SELECT * FROM clientes WHERE codigo=?", lab["cliente"]["codigo"])
    ej("DELETE FROM segmentos WHERE tipo IN ('infraestructura', 'ambiente', 'servicio')")
    if c:
        cargar_gobierno(c["id"], {"segmentos": lab["segmentos"]})
        for u in lab["usuarios"]:
            uu = q1("SELECT id FROM usuarios WHERE usuario=?", u["usuario"])
            for p in u["perfiles"] if uu else []:
                ej("UPDATE perfiles SET segmentos=?, ambientes=? WHERE usuario_id=? AND cliente_id=?",
                   json.dumps(p["segmentos"], ensure_ascii=False), json.dumps(p.get("ambientes", ["*"])), uu["id"], c["id"])
    for p in q("SELECT * FROM politicas"):
        a = json.loads(p["aplica"] or "{}")
        amb = {"produccion" if s == "Producción" else s.lower() for s in a.get("segmentos", []) if s in ("Producción", "QA", "Desarrollo")}
        if amb:
            a["segmentos"], a["ambientes"] = ["*"], sorted(amb)
            ej("UPDATE politicas SET aplica=? WHERE id=?", json.dumps(a, ensure_ascii=False), p["id"])
    ajuste("migracion_segmentos_negocio", ahora())


def migrar_skills_sin_so():
    """28/10: el sistema operativo deja de ser skill (va en «Sistema»); las políticas usan «sistemas»."""
    from .bd import ajuste
    if ajuste("migracion_skills_sin_so"):
        return
    ej("DELETE FROM skills WHERE nombre IN ('linux', 'windows')")
    for c in q("SELECT id FROM clientes"):
        cargar_gobierno(c["id"], {"skills": [s for s in yaml.safe_load(open(os.path.join(DATOS, "gobierno-base.yml")))["skills"]
                                             if s["nombre"] == "iis"]})
    for p in q("SELECT * FROM politicas"):
        a = json.loads(p["aplica"] or "{}")
        so = [s for s in a.get("skills", []) if s in ("linux", "windows")]
        if so:
            a["sistemas"] = so
            a["skills"] = [s for s in a["skills"] if s not in so] or ["*"]
            ej("UPDATE politicas SET aplica=? WHERE id=?", json.dumps(a, ensure_ascii=False), p["id"])
    for e in q("SELECT id, skills_auto, skills_manual FROM equipos"):
        ej("UPDATE equipos SET skills_auto=?, skills_manual=? WHERE id=?",
           json.dumps([s for s in json.loads(e["skills_auto"] or "[]") if s not in ("linux", "windows")]),
           json.dumps([s for s in json.loads(e["skills_manual"] or "[]") if s not in ("linux", "windows")]), e["id"])
    ajuste("migracion_skills_sin_so", ahora())
