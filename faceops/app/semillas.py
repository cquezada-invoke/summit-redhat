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
        ej("INSERT INTO segmentos(cliente_id, nombre, tipo, regla, descripcion, orden, sistema) VALUES (?,?,?,?,?,?,?) "
           "ON CONFLICT(cliente_id, nombre) DO UPDATE SET sistema=excluded.sistema WHERE segmentos.sistema=''",
           cid, s["nombre"], s.get("tipo", "negocio"), json.dumps(s.get("regla", {}), ensure_ascii=False),
           s.get("descripcion", ""), s.get("orden", 0), s.get("sistema", ""))
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
        "segmentos": [{"nombre": s["nombre"], "sistema": s.get("sistema", ""), "orden": s["orden"], "descripcion": s["descripcion"],
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
    migrar_skills_producto()
    migrar_gobierno_identidades(lab)
    migrar_usuarios_lab(lab)
    migrar_skills_plataforma()
    try:
        from .politicas_seg import cargar as cargar_politicas, iniciar as iniciar_politicas
        iniciar_politicas()
        from .cves import iniciar as iniciar_cves
        iniciar_cves()
        from .infraestructuras import iniciar as iniciar_infra, sembrar as sembrar_infra
        iniciar_infra()
        for cl_ in q("SELECT id FROM clientes"):
            sembrar_infra(cl_["id"])
        for cl_ in q("SELECT id FROM clientes"):
            try:
                from .politicas_seg import sincronizar as sinc_pol
                sinc_pol(cl_["id"], forzar=True)   # Git es la fuente de verdad (si no responde, el archivo base)
            except Exception:  # noqa: BLE001
                cargar_politicas(cl_["id"])
    except Exception:  # noqa: BLE001
        import traceback; traceback.print_exc()
    try:
        from .economia import pendientes
        pendientes()   # tickets cerrados antes de los créditos: se liquidan una vez
    except Exception:  # noqa: BLE001
        pass
    cl = q1("SELECT * FROM clientes WHERE codigo=?", c["codigo"])
    if cl:  # arquitecturas versionadas en Git (datos/arquitectura): entran como versión aprobada si cambiaron
        from .arquitectura import cargar_desde_git
        try:
            cargar_desde_git(cl)
        except Exception as e:  # noqa: BLE001
            print("arquitecturas:", e)


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


def migrar_skills_producto():
    """29/10: una skill es el producto concreto habilitado (nginx, postgres, keycloak…). Se renombran las genéricas
    y se quitan «web», «monitoreo» y «cumplimiento» (este último es un control, no un producto)."""
    from .bd import ajuste
    if ajuste("migracion_skills_producto"):
        return
    renombre = {"sso": "keycloak", "iga": "midpoint", "directorio": "389-directory-server", "idm": "red-hat-idm",
                "contenedores": "podman"}
    quitar = {"web", "monitoreo", "cumplimiento"}
    ej(f"DELETE FROM skills WHERE nombre IN ({','.join('?' * len(set(renombre) | quitar))})", *(set(renombre) | quitar))
    base = yaml.safe_load(open(os.path.join(DATOS, "gobierno-base.yml")))["skills"]
    for c in q("SELECT id FROM clientes"):
        cargar_gobierno(c["id"], {"skills": base})

    def cambiar(lista):
        return sorted({renombre.get(x, x) for x in lista if x not in quitar})
    for e in q("SELECT id, skills_auto, skills_manual, skills_habilitadas FROM equipos"):
        hab = json.loads(e["skills_habilitadas"] or "{}")
        hab = {renombre.get(k, k): v for k, v in hab.items() if k not in quitar}
        ej("UPDATE equipos SET skills_auto=?, skills_manual=?, skills_habilitadas=? WHERE id=?",
           json.dumps(cambiar(json.loads(e["skills_auto"] or "[]"))), json.dumps(cambiar(json.loads(e["skills_manual"] or "[]"))),
           json.dumps(hab, ensure_ascii=False), e["id"])
    for p in q("SELECT id, aplica FROM politicas"):
        a = json.loads(p["aplica"] or "{}")
        if any(x in renombre or x in quitar for x in a.get("skills", [])):
            a["skills"] = cambiar(a["skills"]) or ["*"]
            ej("UPDATE politicas SET aplica=? WHERE id=?", json.dumps(a, ensure_ascii=False), p["id"])
    for a in q("SELECT id, skills FROM automatizaciones WHERE skills LIKE '%web%'"):
        ej("UPDATE automatizaciones SET skills=? WHERE id=?",
           json.dumps(sorted(set(json.loads(a["skills"]) or []) - {"web"} | {"nginx", "httpd"})), a["id"])
    from .inventario import calcular, evaluar_hallazgos
    for c in q("SELECT * FROM clientes"):
        try:
            calcular(c)
            evaluar_hallazgos(c)
        except Exception:  # noqa: BLE001 — se recalcula igual en la próxima sincronización
            pass
    ajuste("migracion_skills_producto", ahora())


def migrar_gobierno_identidades(lab):
    """29/10: un segmento de negocio es la aplicación completa aunque mezcle sistemas: «Identidad y accesos» (Linux)
    y «Directorio corporativo» (Windows) pasan a ser «Gobierno de identidades»."""
    from .bd import ajuste
    if ajuste("migracion_gobierno_identidades"):
        return
    c = q1("SELECT * FROM clientes WHERE codigo=?", lab["cliente"]["codigo"])
    if c:
        ej("DELETE FROM segmentos WHERE cliente_id=? AND nombre IN ('Identidad y accesos', 'Directorio corporativo')", c["id"])
        cargar_gobierno(c["id"], {"segmentos": [s for s in lab["segmentos"] if s["nombre"] == "Gobierno de identidades"]})
        for p in q("SELECT id, aplica FROM politicas WHERE cliente_id=?", c["id"]):
            a = json.loads(p["aplica"] or "{}")
            seg = a.get("segmentos", [])
            if any(x in ("Identidad y accesos", "Directorio corporativo") for x in seg):
                a["segmentos"] = sorted({("Gobierno de identidades" if x in ("Identidad y accesos", "Directorio corporativo") else x) for x in seg})
                ej("UPDATE politicas SET aplica=? WHERE id=?", json.dumps(a, ensure_ascii=False), p["id"])
        viejos = ("Identidad y accesos", "Directorio corporativo")
        for p in q("SELECT id, segmentos FROM perfiles WHERE cliente_id=?", c["id"]):
            seg = json.loads(p["segmentos"] or "[]")
            if any(x in viejos for x in seg):
                nuevo = [x for x in seg if x not in viejos] + ["Gobierno de identidades"]
                ej("UPDATE perfiles SET segmentos=? WHERE id=?", json.dumps(nuevo, ensure_ascii=False), p["id"])
    ajuste("migracion_gobierno_identidades", ahora())


def migrar_usuarios_lab(lab):
    """Usuarios del laboratorio que se agregaron después (p. ej. los roles de Arquitectura e Infraestructura)."""
    c = q1("SELECT id FROM clientes WHERE codigo=?", lab["cliente"]["codigo"])
    if not c:
        return
    for u in lab["usuarios"]:
        uid, _ = crear_usuario(u["usuario"], u["nombre"])
        for p in u["perfiles"]:
            if not q1("SELECT id FROM perfiles WHERE usuario_id=? AND cliente_id=?", uid, c["id"]):
                ej("INSERT INTO perfiles(usuario_id, cliente_id, segmentos, ambientes, modo, aprobador, roles) VALUES (?,?,?,?,?,?,?)",
                   uid, c["id"], json.dumps(p["segmentos"], ensure_ascii=False), json.dumps(p.get("ambientes", ["*"])), p["modo"],
                   p["aprobador"], json.dumps(p.get("roles", [])))
            elif p.get("roles"):
                ej("UPDATE perfiles SET roles=? WHERE usuario_id=? AND cliente_id=?", json.dumps(p["roles"]), uid, c["id"])


PLATAFORMA = {  # productos de la plataforma (se declaran en AAP con faceops_skills; no se detectan por servicio)
    "gitea": "Gitea: repositorios Git (estructura como código y respaldos de los servidores)",
    "registro-paquetes": "Registro de paquetes: RPM para dnf e instaladores (ISO, imágenes y bundles)",
    "automation-hub": "Private Automation Hub: registry de contenedores y colecciones de Ansible",
    "aap": "Red Hat Ansible Automation Platform",
    "certificate-system": "CA interna (Red Hat Certificate System / Dogtag PKI)",
    "kvm": "Hipervisor KVM (libvirt) del centro de datos",
    "vault": "Bóveda de contraseñas y secretos (HashiCorp Vault)",
}


def migrar_skills_plataforma():
    for c in q("SELECT id FROM clientes"):
        for n, d in PLATAFORMA.items():
            ej("INSERT OR IGNORE INTO skills(cliente_id, nombre, descripcion) VALUES (?,?,?)", c["id"], n, d)
