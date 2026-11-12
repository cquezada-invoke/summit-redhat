# Diagnóstico: ¿quién generó las aprobaciones pendientes de «Operación y observabilidad»? (solo lectura, sin secretos)
python3 - <<'PY'
import base64, collections, glob, json, re, ssl, urllib.request
inv = glob.glob("/home/cquezada/aap/ansible-automation-platform-containerized-setup-bundle*/inventory-demo")[0]
cl = re.search(r"^gateway_admin_password=(.*)$", open(inv).read(), re.M).group(1).strip()
A = "Basic " + base64.b64encode(f"admin:{cl}".encode()).decode(); ctx = ssl._create_unverified_context()
def g(u):
    with urllib.request.urlopen(urllib.request.Request("https://localhost/api/controller/v2/" + u, headers={"Authorization": A}), context=ctx, timeout=60) as r: return json.loads(r.read())
r = g("workflow_approvals/?status=pending&page_size=200&order_by=created")
c = collections.Counter(); wfs = collections.Counter(); horas = collections.Counter(); lanz = collections.Counter()
for x in r["results"]:
    if "Operaci" not in x["name"]: continue
    wj = (x.get("summary_fields") or {}).get("source_workflow_job") or {}
    c[x["name"][:90]] += 1; wfs[wj.get("name", "")[:70]] += 1; horas[x["created"][:13]] += 1
print("total pendientes", r["count"])
print("nombres", c.most_common(6)); print("workflows", wfs.most_common(8)); print("horas", sorted(horas.items())[-8:])
PY
sudo podman exec -i faceops python3 - <<'Q' 2>/dev/null | tail -12
from app import create_app
create_app()
from app.bd import q
for r in q("SELECT a.nombre_aap, a.categoria, t.estado, COUNT(*) n, MIN(t.creado) desde, MAX(t.creado) hasta, MIN(t.motivo) motivo FROM tickets t JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE t.estado='pendiente_aprobacion' GROUP BY a.nombre_aap, t.estado ORDER BY n DESC LIMIT 10"):
    print("FO", r["n"], r["nombre_aap"][:50], "|", r["categoria"], "|", r["desde"][:16], "→", r["hasta"][:16], "|", (r["motivo"] or "")[:80])
print("eventos autocuidado con ticket:", q("SELECT COUNT(*) n FROM autocuidado_eventos WHERE ticket_id IS NOT NULL")[0]["n"] if q("SELECT name FROM sqlite_master WHERE name='autocuidado_eventos'") else "sin tabla")
Q
