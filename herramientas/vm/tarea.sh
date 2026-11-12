set -e
cd ~/summit-redhat && git log -1 --format='%h %s' | cut -c1-80
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v Warn | tail -80
import time, json
from app import create_app
app = create_app()
c = app.test_client()
from app.bd import q1
u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'")
with c.session_transaction() as s: s["uid"]=u["id"]; s["csrf"]="x"
for amb in ["produccion","qa","desarrollo"]:
    t=time.time(); r=c.get("/mapa/datos?vista=negocio&nivel=segmento&valor=App%20de%20pagos&ambiente="+amb, headers={"X-Requested-With":"fetch"})
    print("==",amb,r.status_code,round(time.time()-t,2))
    s=(r.get_json() or {}).get("segmento") or {}
    print(" pct",s.get("salud_pct"),"eq",s.get("equipos"),[(a["area"],a["pct"]) for a in s.get("salud_areas",[])])
    for x in s.get("salud",[]): print("  ",x["nivel"],x["area"],"|",x["titulo"],"|",(x.get("detalle") or "")[:120],"|",x.get("accion"),"|",x.get("url",""))
r=c.get("/mapa?vista=aplicaciones&segmento=App%20de%20pagos&ambiente=qa")
print(r.status_code, r.headers.get("Location"))
Q
