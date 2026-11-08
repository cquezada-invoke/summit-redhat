"""C3 del plan de inventario: cambia un archivo de /etc por FaceOps (aplica una política pendiente) y deja el ticket."""
import json, sys, time
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app.politicas_seg import tablero, solicitar
    from app.operacion import actualizar_tickets, resolver_aprobacion
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    e = q1("SELECT * FROM equipos WHERE nombre=?", sys.argv[1])
    pols, por = tablero(cl["id"], [dict(e)])
    cand = [x for x in pols if any(y["equipo"]["id"] == e["id"] and y["estado"] != "al_dia" for y in x["equipos"])]
    p = next((x for x in cand if x["codigo"] in sys.argv[2:]), None) if len(sys.argv) > 2 else (cand[0] if cand else None)
    if not p:
        print("RESC3 sin política pendiente"); sys.exit(0)
    t = solicitar(u, cl, p, [dict(e)], "aplicar", f"Plan de prueba C3 de «Sistema · Inventariar máquina»: cambio por FaceOps ({p['codigo']})")[0]
    for _ in range(90):
        time.sleep(8); actualizar_tickets()
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if x["estado"] == "pendiente_aprobacion":
            try: resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)")
            except Exception: pass
        if x["estado"] in ("exitoso", "fallido", "rechazado", "error"): break
    print("RESC3", p["codigo"], x["numero"], x["estado"])
