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
        prev = ev["casos"].get(caso) or {}
        if prev.get("resultado") == "ok":
            print(caso, "ya ok:", prev.get("ticket")); continue
        if caso == "C2" and (ev["casos"].get("C1") or {}).get("resultado") != "ok":
            print("C2 espera a C1"); continue
        if caso in ("C1", "C2"):
            e = q1("SELECT * FROM equipos WHERE id=131")
        elif caso == "C3":
            e = q1("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND nombre LIKE 'srv-prueba-rhel9%' ORDER BY id LIMIT 1", cl["id"]) \
                or q1("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado' AND inventario='Servidores habilitados' AND lower(so) LIKE '%red hat%' ORDER BY id LIMIT 1", cl["id"])
        else:   # C4: máquina que FaceOps conoce pero que no existe en el anfitrión KVM (registro de prueba, se borra al terminar)
            nom4 = "prueba-vo-inexistente"
            if not q1("SELECT 1 FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], nom4):
                ej("INSERT INTO equipos(cliente_id, nombre, so, so_familia, categoria, forma, estado, ambiente, ubicacion, inventario, inventarios, ip, visto) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))", cl["id"], nom4, "Red Hat Enterprise Linux 9", "RedHat", "servidor", "virtual",
                   "sin datos", "desarrollo", "Centro de datos Valparaíso", "Servidores habilitados", '["Servidores habilitados"]', "pendiente")
            e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cl["id"], nom4)
        if not e:
            print(caso, "sin máquina para el caso"); continue
        antes = q1("SELECT MAX(id) m FROM tickets")["m"] or 0
        print(f"== {caso} sobre {e['nombre']} (so={e['so']}, ip={e['ip']}, energía={e['energia']})")
        if prev.get("tid") and not prev.get("resultado"):   # reanudar: el ticket sigue en AAP aunque el arnés se haya cortado
            t = q1("SELECT * FROM tickets WHERE id=?", prev["tid"]); antes = prev.get("antes", antes); e = dict(e, ip=prev.get("ip0", e["ip"]))
            print("  reanuda", t["numero"], t["estado"])
        else:
            try:
                t = lanzar(cl, u, a, e, {"vo_maquina": e["nombre"], "vo_usuario": "faceops"}, f"Plan de prueba {caso}: Vincular máquina on-premise")
            except Exception as x:
                print("  no se lanzó:", str(x)[:200]); ev["casos"][caso] = {"resultado": "fallido", "nota": str(x)[:200]}; guardar(ev); continue
            ev["casos"][caso] = {"tid": t["id"], "antes": antes, "ip0": e["ip"]}; guardar(ev)
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
        ok = {"C1": t["estado"] == "exitoso" and bool(acc) and bool(r.get("siguiente")) and r["siguiente"][1] == "exitoso" and bool(e2["ultima_recoleccion"]) and "windows" in (e2["so"] or "").lower(),
              "C2": t["estado"] == "exitoso" and e2["ip"] == e["ip"],
              "C3": t["estado"] == "exitoso" and bool(re.match(r"^\d", e2["ip"] or "")),
              "C4": t["estado"] == "fallido" and "No existe la máquina" in (t["resultado"] or "")}[caso]
        r["resultado"] = "ok" if ok else "fallido"
        print("  =>", r["resultado"])
        ev["casos"][caso] = r
        guardar(ev)
        if caso == "C4":
            ej("DELETE FROM equipos WHERE cliente_id=? AND nombre='prueba-vo-inexistente'", cl["id"])
    print("EVIDENCIA", json.dumps(ev, ensure_ascii=False)[:900])
    if all((ev["casos"].get(c) or {}).get("resultado") for c in sys.argv[1:]):
        print("FIN-TODO")
ARN
)
echo "$B64" | base64 -d > /tmp/arnes_vo.py
for i in $(seq 1 40); do sudo podman exec faceops grep -q "_servicio-windows" /opt/faceops/app/operacion.py 2>/dev/null && break; sleep 30; done
sudo podman exec faceops grep -c "_servicio-windows" /opt/faceops/app/operacion.py
sed -i 's/arnes_vo.py C4 C1 C2/arnes_vo.py C4 C3 C1 C2/' /tmp/loop-vo.sh
grep -n "arnes_vo.py" /tmp/loop-vo.sh
rm -f /tmp/arnes-vo.log
sudo systemd-run --quiet --unit=arnes-vo-$(date +%s) bash /tmp/loop-vo.sh && echo lanzado
for i in $(seq 1 90); do grep -q -E "FIN-LOOP" /tmp/arnes-vo.log && break; grep -q "== C1" /tmp/arnes-vo.log && grep -A3 "== C1" /tmp/arnes-vo.log | grep -q "siguiente" && break; sleep 30; done
grep -v -i -E "warn|token|password|secret" /tmp/arnes-vo.log | tail -30
