cd ~/summit-redhat && git pull -q --ff-only origin main; git log --oneline -1 | cut -c1-60
python3 faceops/aap/configurar.py 2>&1 | grep -E "imágenes \(uso|Mover imágenes|Traceback" | head -4
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 15
B64=$(base64 -w0 <<'ARN'
"""Arnés de prueba (regla de Cristian): casos de «FaceOps · Vincular máquina on-premise» como tickets reales por AAP.
Uso: python3 arnes_vo.py CASO [CASO...]   (C1 C2 C3 C4). Guarda la evidencia en /var/lib/faceops/prueba-vo.json."""
import json, os, re, sys, time
from app import create_app
from app.bd import q, q1, j, ej
app = create_app()
ARCH = "/var/lib/faceops/prueba-vo.json"
NOMBRE = "FaceOps · Vincular máquina on-premise"


def evid():
    try:
        return json.load(open(ARCH))
    except Exception:
        return {}


def guardar(d):
    json.dump(d, open(ARCH, "w"), ensure_ascii=False, indent=1)


def esperar(tid, minutos):
    from app.operacion import resolver_aprobacion
    u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
    fin = time.time() + minutos * 60
    while time.time() < fin:
        t = q1("SELECT * FROM tickets WHERE id=?", tid)
        if t["estado"] == "pendiente_aprobacion":
            try:
                resolver_aprobacion(u, tid, True, "Aprobado por Claude (regla de Cristian)")
            except Exception as x:
                print("  aprobación:", str(x)[:120])
        if t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"):
            return t
        time.sleep(10)
    return q1("SELECT * FROM tickets WHERE id=?", tid)


def lanzar(cl, u, a, e, params, motivo):
    from flask import g
    from app.operacion import solicitar_en_equipo
    with app.test_request_context("/"):
        g.usuario, g.cliente, g.perfil = u, cl, {"modo": "escritura", "segmentos": ["*"]}
        return solicitar_en_equipo(u, cl, a, dict(e), params, motivo, prueba=True)


def inventario_de(cl, nombre, desde_id, minutos):
    """El inventario automático que lanza FaceOps tras vincular (ticket con motivo de inventario)."""
    fin = time.time() + minutos * 60
    while time.time() < fin:
        t = q1("SELECT t.* FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.cliente_id=? AND t.id>? "
               "AND a.nombre_aap IN ('Sistema · Inventariar máquina','FaceOps · Vincular máquina a la bóveda') AND t.limite=? ORDER BY t.id DESC LIMIT 1",
               cl["id"], desde_id, nombre)
        if t and t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"):
            return t
        if t and t["estado"] == "pendiente_aprobacion":
            from app.operacion import aprobar_vinculaciones
            aprobar_vinculaciones()
        time.sleep(15)
    return t if 't' in dir() else None


with app.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], NOMBRE)
    if not a:
        from app.operacion import sincronizar_catalogo
        sincronizar_catalogo(cl)
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], NOMBRE)
    from app.pruebas import estado
    print("estado:", estado(dict(a, cliente_id=cl["id"]))["estado"], estado(dict(a, cliente_id=cl["id"]))["huella"][:12])
    ev = evid()
    huella = estado(dict(a, cliente_id=cl["id"]))["huella"]
    if ev.get("huella") != huella:
        ev = {"huella": huella, "casos": {}}
    for caso in sys.argv[1:]:
        if caso in ("C1", "C2"):
            e = q1("SELECT * FROM equipos WHERE id=131")
        elif caso == "C3":
            e = q1("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND nombre LIKE 'srv-prueba-rhel9%' ORDER BY id LIMIT 1", cl["id"]) \
                or q1("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND inventario='Servidores habilitados' AND lower(so) LIKE '%red hat%' ORDER BY id LIMIT 1", cl["id"])
        else:   # C4: una máquina que FaceOps conoce pero que no está en el anfitrión KVM (nube)
            from app.vistas import energia_posible   # una máquina que FaceOps conoce pero que no es del anfitrión KVM (vSphere o física)
            e = next((x for x in q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND categoria='servidor' ORDER BY id", cl["id"])
                      if not energia_posible(x) and "kvm" not in (x.get("skills") or "") and not x["nombre"].startswith("anfitrion")), None)
        if not e:
            print(caso, "sin máquina para el caso"); continue
        antes = q1("SELECT MAX(id) m FROM tickets")["m"] or 0
        print(f"== {caso} sobre {e['nombre']} (so={e['so']}, ip={e['ip']}, energía={e['energia']})")
        try:
            t = lanzar(cl, u, a, e, {"vo_maquina": e["nombre"], "vo_usuario": "faceops"}, f"Plan de prueba {caso}: Vincular máquina on-premise")
        except Exception as x:
            print("  no se lanzó:", str(x)[:200]); ev["casos"][caso] = {"resultado": "fallido", "nota": str(x)[:200]}; guardar(ev); continue
        t = esperar(t["id"], 45 if caso in ("C1", "C2") else 25)
        print("  ticket", t["numero"], t["estado"], "|", re.sub(r"\s+", " ", (t["resultado"] or ""))[-400:])
        r = {"ticket": t["numero"], "estado": t["estado"]}
        if caso in ("C1", "C2", "C3") and t["estado"] == "exitoso":
            ti = inventario_de(cl, e["nombre"], antes, 25 if caso != "C2" else 20)
            r["siguiente"] = ti and (ti["numero"], ti["estado"])
            print("  siguiente:", r["siguiente"])
        e2 = q1("SELECT * FROM equipos WHERE id=?", e["id"])
        print("  equipo ahora: so=", e2["so"], "| ip=", e2["ip"], "| recolectado=", e2["ultima_recoleccion"], "| energía=", e2["energia"])
        from app.boveda import acceso
        acc = acceso(e["nombre"]) or {}
        print("  bóveda:", {k: acc.get(k) for k in ("usuario", "protocolo", "puerto", "conexion")}, "clave" in acc)
        r.update(so=e2["so"], ip=e2["ip"], boveda=bool(acc))
        ok = {"C1": t["estado"] == "exitoso" and bool(acc) and r.get("siguiente") and r["siguiente"][1] == "exitoso" and "windows" in (e2["so"] or "").lower(),
              "C2": t["estado"] == "exitoso" and e2["ip"] == e["ip"],
              "C3": t["estado"] == "exitoso" and bool(re.match(r"^\d", e2["ip"] or "")),
              "C4": t["estado"] == "fallido" and "No existe la máquina" in (t["resultado"] or "")}[caso]
        r["resultado"] = "ok" if ok else "fallido"
        print("  =>", r["resultado"])
        ev["casos"][caso] = r
        guardar(ev)
    print("EVIDENCIA", json.dumps(ev, ensure_ascii=False)[:900])
ARN
)
B64I=$(base64 -w0 <<'ARN'
"""Arnés de prueba (regla de Cristian) de «FaceOps · Incorporar máquinas»: casos C1–C6 como tickets reales por AAP.
Uso: python3 arnes_inc.py info | CASO [CASO...]. Evidencia en /var/lib/faceops/prueba-inc.json."""
import json, re, sys, time
from app import create_app
from app.bd import q, q1, j
app = create_app()
ARCH = "/var/lib/faceops/prueba-inc.json"
NOMBRE = "FaceOps · Incorporar máquinas"


def esperar(tid, minutos):
    from app.operacion import resolver_aprobacion
    u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
    fin = time.time() + minutos * 60
    while time.time() < fin:
        t = q1("SELECT * FROM tickets WHERE id=?", tid)
        if t["estado"] == "pendiente_aprobacion":
            try:
                resolver_aprobacion(u, tid, True, "Aprobado por Claude (regla de Cristian)")
            except Exception as x:
                print("  aprobación:", str(x)[:120])
        if t["estado"] in ("exitoso", "fallido", "cancelado", "rechazado"):
            return t
        time.sleep(10)
    return q1("SELECT * FROM tickets WHERE id=?", tid)


with app.app_context():
    from flask import g
    from app.operacion import solicitar, solicitar_en_equipo, artefactos_ticket
    from app.aap import AAP
    from app.pruebas import estado
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    u = q1("SELECT * FROM usuarios WHERE es_admin=1 AND activo=1 ORDER BY id LIMIT 1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], NOMBRE)
    st = estado(dict(a, cliente_id=cl["id"]))
    print("estado:", st["estado"], st["huella"][:12])
    infs = q("SELECT id,nombre,tipo,inventario,credencial,detalle FROM infraestructuras WHERE cliente_id=? AND tipo IN ('gcp','azure','aws')", cl["id"])
    for i in infs:
        print("nube:", i["id"], i["nombre"], i["tipo"], i["inventario"], "cred:", bool(i["credencial"]), {k: v for k, v in j(i["detalle"], {}).items() if k in ("proyecto", "zona", "etiqueta")})
    gcp = [x for x in q("SELECT id,nombre,so,ip,energia,inventario FROM equipos WHERE cliente_id=? AND estado!='retirado'", cl["id"])
           if any(x["inventario"] == i["inventario"] for i in infs)]
    for x in gcp:
        print("vm nube:", x["id"], x["nombre"], x["so"], x["ip"], x["energia"])
    if sys.argv[1:] == ["info"]:
        sys.exit(0)
    try:
        ev = json.load(open(ARCH))
    except Exception:
        ev = {}
    if ev.get("huella") != st["huella"]:
        ev = {"huella": st["huella"], "casos": {}}
    inf = next(i for i in infs if i["tipo"] == "gcp")
    aap = AAP.de_cliente(cl)
    for caso in sys.argv[1:]:
        params = {"inc_infraestructura": inf["nombre"], "inc_usuario": "faceops"}
        e = None
        if caso in ("C4", "C5"):
            e = next((x for x in gcp if "windows" in (x["so"] or "").lower()), None)
            if not e:
                print(caso, "no hay VM Windows en la nube"); continue
            params["inc_maquina"] = e["nombre"]
        elif caso == "C6":
            params["inc_maquina"] = "no-existe-faceops-prueba"
        elif caso == "C3":
            params["inc_infraestructura"] = inf["nombre"]
        print(f"== {caso}", params)
        with app.test_request_context("/"):
            g.usuario, g.cliente, g.perfil = u, cl, {"modo": "escritura", "segmentos": ["*"]}
            if e:
                t = solicitar_en_equipo(u, cl, a, dict(q1("SELECT * FROM equipos WHERE id=?", e["id"])), params, f"Plan de prueba {caso}: Incorporar máquinas", prueba=True)
            else:
                t = solicitar(u, cl, a, None, params, f"Plan de prueba {caso}: Incorporar máquinas", None, None, prueba=True)
        t = esperar(t["id"], 40)
        art = artefactos_ticket(aap, t) or {}
        print("  ticket", t["numero"], t["estado"], "|", re.sub(r"\s+", " ", t["resultado"] or "")[-500:])
        print("  incorporadas:", art.get("faceops_incorporadas"))
        ev["casos"][caso] = {"ticket": t["numero"], "estado": t["estado"], "maquinas": art.get("faceops_incorporadas")}
        json.dump(ev, open(ARCH, "w"), ensure_ascii=False, indent=1)
    print("EVIDENCIA", json.dumps(ev, ensure_ascii=False)[:1500])
ARN
)
echo "$B64" | base64 -d > /tmp/arnes_vo.py; echo "$B64I" | base64 -d > /tmp/arnes_inc.py
sudo podman cp /tmp/arnes_vo.py faceops:/opt/faceops/arnes_vo.py; sudo podman cp /tmp/arnes_inc.py faceops:/opt/faceops/arnes_inc.py
sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 arnes_inc.py info 2>&1 | grep -v -i -E "warn|token|password|secret" | tail -15
sudo podman exec -d -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops sh -c "python3 arnes_vo.py C4 C1 C2 > /var/lib/faceops/arnes-vo.log 2>&1"
sleep 2700
sudo podman exec faceops sh -c "grep -v -i -E 'warn|token|password|secret' /var/lib/faceops/arnes-vo.log | tail -30"
