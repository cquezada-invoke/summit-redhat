cd ~/summit-redhat
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -40
import re
from app import create_app
from app.bd import q, q1
app = create_app()
with app.app_context():
    from flask import g
    from app import reportes as R
    from app.grupos_dueno import grupos, guardar_grupo
    c = q1("SELECT * FROM clientes WHERE codigo='lab'"); g.cliente = c
    gs = grupos(c["id"])
    print("correos:", sorted({x["correo"] or "(vacío)" for x in gs}))
    for x in gs:
        if not x["correo"]:
            guardar_grupo(c["id"], x["clave"], "cquezada@redhat.com", x.get("descripcion") or "", "cquezada")
    print("SMTP configurado:", R.os.environ.get("FACEOPS_SMTP_HOST") is not None)
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    r = cl.get("/lab/gobierno/reportes?area=servidores"); print("pagina", r.status_code)
    with app.app_context():
        from flask import g
        g.cliente = q1("SELECT * FROM clientes WHERE codigo='lab'")
        for a in R.calcular(g.cliente, guardar=False):
            print(f"{a['nombre']}: {a['total']} % ({a['n']} obj) · " + " · ".join(f"{p['nombre'][:4]} {p['pct']}" for p in a["pilares"]))
        ini = q("SELECT seccion, estado, COUNT(*) n FROM reporte_iniciativas WHERE cliente_id=? GROUP BY 1,2", g.cliente["id"])
        print("iniciativas:", ini, "total", sum(x["n"] for x in ini))
    r = cl.post("/lab/gobierno/reportes/enviar", data={"_csrf": "x", "area": "servidores"}); print("enviar", r.status_code)
    e = q1("SELECT fecha, tipo, para, asunto, total, resultado FROM reporte_envios ORDER BY id DESC LIMIT 1"); print("envio:", e)
    h = cl.get("/lab/monitoreo/itil").get_data(as_text=True)
    for k in ("Mejora continua", "Medición y reporte"):
        i = h.find(k + "</"); t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[i:i+2500]))
        print("ITIL", k, "|", t[:120], "| Falta" in t or "Falta:" in t, "| medido:", re.search(r"Medido: [^|]{0,160}", t).group(0) if "Medido:" in t else None)
Q
