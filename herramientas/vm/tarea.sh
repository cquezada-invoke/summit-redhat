cd ~/summit-redhat && git log --oneline -1 | cut -c1-60
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -150
import json, re
from app import create_app
app = create_app()
SEC = re.compile(r"pass|clave|token|secret|key|cred", re.I)
def forma(v, d=0):
    if isinstance(v, dict):
        if d >= 2: return "{%d}" % len(v)
        return {k: ("***" if SEC.search(k) else forma(x, d+1)) for k, x in list(v.items())[:25]}
    if isinstance(v, list): return "[%d]%s" % (len(v), (" " + json.dumps(forma(v[0], 3))[:80]) if v else "")
    s = str(v); return s[:60]
from flask import session
with app.test_request_context("/faceops/lab/plataformas", headers={"X-Requested-With":"fetch"}):
    from app import db as bd
    session["uid"] = 1; session["csrf"] = "x"; session["cliente_id"] = bd.q1("SELECT id FROM clientes WHERE lower(codigo)='lab'")["id"]
    app.preprocess_request()
    from flask import g
    from app import vistas as V
    print("cliente", g.cliente["codigo"])
    par = V._riesgo_parque()
    sos = {"rhel", "so:ubuntu", "windows-server", "so:windows", "so:linux"}
    for e, r in par:
        ps = [c for c in V._plataformas_de(e, r) if c not in sos]
        if not ps: continue
        d = e.get("datos"); d = json.loads(d) if isinstance(d, str) else (d or {})
        print("==", e["id"], e["nombre"], "|", e.get("categoria"), "|", e.get("so"), "|", e.get("energia"), "| skills", e.get("skills"))
        print("   plats", [V.id_plataforma(c, e["id"]) for c in ps])
        print("   datos", json.dumps(forma(d))[:1500])
Q
