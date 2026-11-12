#!/bin/bash
# Certificación Núcleo · fase 2: usuarios locales de prueba (qa.nucleo admin+aprobador, qa.lector solo lectura) con doble factor;
# las claves quedan solo en un archivo 600 del anfitrión y nunca se imprimen.
cat > /tmp/n2u.py <<'PY'
import json, secrets, os
from werkzeug.security import generate_password_hash
from app import create_app
from app.bd import q, q1, ej, ahora, auditar
from app.doble_factor import nuevo_secreto
app = create_app()
with app.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    cols = [r["name"] for r in q("PRAGMA table_info(usuarios)")]
    out = {}
    for usu, nombre, admin, modo, apr in (("qa.nucleo", "QA Certificación Núcleo", 1, "escritura", 1), ("qa.lector", "QA Lector Núcleo", 0, "lectura", 0)):
        clave = secrets.token_urlsafe(18); sec = nuevo_secreto()
        u = q1("SELECT id FROM usuarios WHERE usuario=?", usu)
        if not u:
            ej("INSERT INTO usuarios (usuario, nombre, correo, clave_hash, es_admin, activo, creado) VALUES (?,?,?,?,?,1,?)",
               usu, nombre, "", generate_password_hash(clave), admin, ahora())
            u = q1("SELECT id FROM usuarios WHERE usuario=?", usu)
        ej("UPDATE usuarios SET clave_hash=?, es_admin=?, activo=1, totp_secreto=?, totp_activo=1 WHERE id=?", generate_password_hash(clave), admin, sec, u["id"])
        if "fuente" in cols: ej("UPDATE usuarios SET fuente='' WHERE id=? AND fuente IS NULL", u["id"])
        p = q1("SELECT id FROM perfiles WHERE usuario_id=? AND cliente_id=?", u["id"], cl["id"])
        if p: ej("UPDATE perfiles SET modo=?, aprobador=?, segmentos=? WHERE id=?", modo, apr, '["*"]', p["id"])
        else: ej("INSERT INTO perfiles (usuario_id, cliente_id, segmentos, modo, aprobador) VALUES (?,?,?,?,?)", u["id"], cl["id"], '["*"]', modo, apr)
        if usu == "qa.nucleo":
            for sec_ in ("servidores", "monitoreo", "plataformas", "so"):
                ej("INSERT OR IGNORE INTO grupo_miembros (cliente_id, seccion, usuario_id, rol, desde) VALUES (?,?,?,?,?)", cl["id"], sec_, u["id"], "dueño", ahora())
        auditar("Claude (certificación Núcleo)", cl["id"], "usuario local de prueba", usu, {"admin": admin, "modo": modo})
        out[usu] = {"id": u["id"], "clave": clave, "totp": sec}
    open("/tmp/qa-nucleo.json", "w").write(json.dumps(out)); os.chmod("/tmp/qa-nucleo.json", 0o600)
    for usu in out:
        uu = q1("SELECT id, usuario, es_admin, totp_activo FROM usuarios WHERE usuario=?", usu)
        pp = q1("SELECT modo, aprobador FROM perfiles WHERE usuario_id=? AND cliente_id=?", uu["id"], cl["id"])
        gg = q("SELECT seccion, rol FROM grupo_miembros WHERE usuario_id=? AND cliente_id=?", uu["id"], cl["id"])
        print("USUARIO", uu["id"], uu["usuario"], "admin", uu["es_admin"], "2FA", uu["totp_activo"], "perfil", pp, "grupos", [(g_["seccion"], g_["rol"]) for g_ in gg])
PY
sudo podman cp /tmp/n2u.py faceops:/tmp/n2u.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/n2u.py 2>&1 | grep -v -i -E "token|password|secret|clave|warn" | tail -8
sudo podman cp faceops:/tmp/qa-nucleo.json /tmp/qa-nucleo.json && chmod 600 /tmp/qa-nucleo.json
sudo podman exec faceops rm -f /tmp/n2u.py /tmp/qa-nucleo.json
ls -l /tmp/qa-nucleo.json | awk '{print "credenciales de prueba en archivo", $1, $5" bytes (no se muestran)"}'
