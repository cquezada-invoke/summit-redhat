"""Flotante de salud › Recuperación de una máquina: el botón de «Volúmenes de datos con respaldo» lanza «Recuperación ·
Respaldar volúmenes con restic» (ticket con aprobación, como el endpoint del flotante), queda «En curso · número» y al
terminar «Aplicada · número», y el pilar sube. Corre en el contenedor de FaceOps:
  sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - <equipo> [desde_cero] < respaldo_flotante.py
desde_cero: antes, FaceOps olvida el respaldo registrado (solo su ficha, no la máquina) para ver el botón desde el inicio."""
import json
import sys
import time

from app import create_app

app = create_app()
nombre, cero = sys.argv[1], len(sys.argv) > 2 and sys.argv[2] == "desde_cero"
with app.test_request_context():
    from flask import g
    from app.bd import ej, j, q1
    from app.operacion import actualizar_tickets, resolver_aprobacion, solicitar
    from app.puntos import crear, lista
    from app.salud_acciones import MARCA, _ticket, salud_acciones
    from app.vistas import _radar_maquina
    c = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'") or q1("SELECT * FROM usuarios WHERE id=1")
    g.cliente, g.usuario, g.perfil = c, u, {"modo": "escritura"}
    eq = lambda: dict(q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", c["id"], nombre))

    def rec():
        e = eq()
        r = _radar_maquina(e, [], lista(c["id"], e["id"]))
        p = next(x for x in r["principios"] if x["nombre"] == "Recuperación")
        return e, p

    if cero:
        e = eq()
        d = j(e["datos"], {})
        d.pop("respaldo_datos", None)
        ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])
        crear(c, dict(e, datos=json.dumps(d)), origen="prueba del flotante (estado previo al respaldo)", usuario="Claude", si_cambio=True)
    e, p = rec()
    sug = next((s for s in salud_acciones(e, p) if s.get("id") == "rec_volumenes"), None)
    print("ANTES", json.dumps({"pct": p["pct"], "falta": p["falta"], "sugerencia": sug}, ensure_ascii=False, default=str), flush=True)
    ac = next((x for x in (sug or {}).get("acciones") or [] if x["tipo"] == "auto"), None)
    if not ac or ac.get("bloqueo"):
        print("RESF", json.dumps({"error": "sin botón o bloqueado", "accion": ac}, ensure_ascii=False, default=str))
        sys.exit(0)
    a = dict(q1("SELECT * FROM automatizaciones WHERE id=?", ac["aid"]))
    t = solicitar(u, c, a, None, {}, f"{MARCA}{a['nombre']} en {e['nombre']}", None,
                  {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=e)
    print("TICKET", t["numero"], flush=True)
    en_curso, fin = None, time.time() + 25 * 60
    while time.time() < fin:
        time.sleep(8)
        try:
            actualizar_tickets()
        except Exception:  # noqa: BLE001
            pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if en_curso is None:
            tk = _ticket(c["id"], eq(), a["id"])
            en_curso = tk if tk and tk.get("abierto") else en_curso
        if x["estado"] == "pendiente_aprobacion":
            try:
                resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)")
            except Exception as z:  # noqa: BLE001
                print("aprobación:", str(z)[:200], flush=True)
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error"):
            break
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    e2, p2 = rec()
    tk = _ticket(c["id"], e2, a["id"])
    print("RESF", json.dumps({"ticket": t["numero"], "estado": x["estado"], "en_curso": en_curso, "boton_final": tk,
                              "etiqueta": (f"Aplicada · {tk['numero']}" if tk and not tk["abierto"] and tk["estado"] == "exitoso" else ""),
                              "antes": p["pct"], "despues": p2["pct"], "falta_despues": p2["falta"],
                              "respaldo": (j(e2["datos"], {}).get("respaldo_datos") or {}).get("snapshot", ""),
                              "resultado": (x["resultado"] or "")[-500:]}, ensure_ascii=False, default=str), flush=True)
