set -a; . ~/faceops/faceops.env; set +a
GT=$(grep '^gitea_token=' ~/demo-drift/cicd-secretos.txt | cut -d= -f2-)
G=http://127.0.0.1:3000/api/v1/repos/invoke/red-laboratorio
cat > /tmp/rt.py <<'PY'
import sys, time
from app import create_app
from app.bd import q1
a = create_app()
APR = "Aprobado por Claude (regla de Cristian)"; FIN = ("exitoso", "fallido", "cancelado", "rechazado", "error")
nom, eq, et = sys.argv[1], sys.argv[2], sys.argv[3]
with a.test_request_context("/"):
    from flask import g
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada' AND es_admin=1"); cli = q1("SELECT * FROM clientes WHERE codigo='lab'")
    g.usuario, g.cliente = u, cli
    from app.operacion import solicitar_en_equipo, resolver_aprobacion
    au = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cli["id"], nom)
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=?", cli["id"], eq)
    if nom == "SEGUIR":
        t = {"id": int(eq)}
    else:
        try:
            t = solicitar_en_equipo(u, cli, au, e, {}, f"Plan de prueba · {nom} · {et}", prueba=True)
        except Exception as x:
            import traceback; tb = traceback.format_exc().strip().splitlines()
            print("RES - no-lanzado", str(x)[:150], "|", " / ".join(l.strip() for l in tb[-5:-1])[:400]); sys.exit()
    print("TID", t["id"], flush=True)
    ok = False
    for _ in range(90):
        tk = q1("SELECT numero, estado, aap_job_id, resultado FROM tickets WHERE id=?", t["id"])
        if tk["estado"] == "pendiente_aprobacion" and not ok:
            resolver_aprobacion(u, t["id"], True, APR); ok = True
        if tk["estado"] in FIN: break
        time.sleep(8)
    print("RES", tk["numero"], tk["estado"], tk["aap_job_id"] or 0, (tk["resultado"] or "")[:200].replace("\n", " "))
PY
sudo podman cp /tmp/rt.py faceops:/tmp/rt.py
listo(){ for i in $(seq 1 60); do sudo podman exec faceops test -d /opt/faceops/app 2>/dev/null && sudo podman cp /tmp/rt.py faceops:/tmp/rt.py 2>/dev/null && return; sleep 5; done; }
correr(){ listo; R=$(sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/rt.py "$1" "$2" "$3" 2>&1 | grep -v -i warn | tee /tmp/rt-ult.txt | grep '^RES' | tail -1)
  for k in 1 2 3; do [ -n "$R" ] && break; TID=$(grep '^TID' /tmp/rt-ult.txt | awk '{print $2}'); [ -z "$TID" ] && { tail -2 /tmp/rt-ult.txt | cut -c1-200; break; }
    sleep 20; listo; R=$(sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/rt.py SEGUIR "$TID" x 2>&1 | grep -v -i warn | tee /tmp/rt-ult2.txt | grep '^RES' | tail -1); done
  echo "$3 · $2: $(echo "$R" | cut -c5-220)"; J=$(echo "$R" | awk '{print $4}'); case "$J" in ''|*[!0-9]*) J=0;; esac
  echo "$R" | awk '{print $2}' >> /tmp/red-tickets.txt
  [ "${J:-0}" != 0 ] && python3 - "$J" <<'PY'
import os, ssl, sys, urllib.request, re
cx = ssl._create_unverified_context(); B = os.environ["FACEOPS_AAP_URL"].rstrip("/") + "/api/controller/v2/"
H = {"Authorization": "Bearer " + os.environ["FACEOPS_AAP_TOKEN"]}
import json
def get(u): return urllib.request.urlopen(urllib.request.Request(B + u, headers=H), context=cx).read().decode(errors="replace")
try:
    t = get(f"jobs/{sys.argv[1]}/stdout/?format=txt")
except Exception:
    t = ""
    try:
        for n in json.loads(get(f"workflow_jobs/{sys.argv[1]}/workflow_nodes/"))["results"]:
            jid = (n.get("summary_fields", {}).get("job") or {})
            if jid.get("type") == "job":
                t += get(f"jobs/{jid['id']}/stdout/?format=txt")
    except Exception as x:
        print("   (sin salida del job:", str(x)[:60], ")")
if re.search(r"\$6\$|\$1\$|encrypted-password \S{8,}", t) and "<oculto" not in t: print("   !! posible secreto en la salida")
for l in t.splitlines():
    if re.search(r'"msg"|líneas|CAMBIOS|sin cambios|No se pudo|línea base|fatal|FAILED|^\s+"[+-] ', l) and not re.search(r"(?i)password|token|secret", l):
        print("   ", l.strip()[:200])
PY
}
gcfg(){ curl -s -H "Authorization: token $GT" "$G/contents/respaldos/$1.cfg?ref=main"; }
gtxt(){ gcfg $1 | python3 -c "import sys,json,base64;d=json.load(sys.stdin);print(base64.b64decode(d['content']).decode()) if 'content' in d else print('NO-EXISTE')"; }
gcommits(){ curl -s -H "Authorization: token $GT" "$G/commits?path=respaldos/$1.cfg&limit=50" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null; }
: > /tmp/red-tickets.txt
cat > /tmp/aprob.py <<'PY2'
from app import create_app
from app.bd import q, q1
a = create_app()
with a.test_request_context("/"):
    from app.operacion import resolver_aprobacion
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada' AND es_admin=1")
    for t in q("SELECT id, numero FROM tickets WHERE motivo='diag' AND estado='pendiente_aprobacion'"):
        resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); print("aprobado", t["numero"])
PY2
sudo podman cp /tmp/aprob.py faceops:/tmp/aprob.py; sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops python3 /tmp/aprob.py 2>&1 | grep aprobado; sleep 60
# limpiar estado previo (sin línea base para sw-acc-01 y suc1)
for h in sw-acc-01 suc1; do SHA=$(gcfg $h | python3 -c "import sys,json;print(json.load(sys.stdin).get('sha',''))" 2>/dev/null); [ -n "$SHA" ] && curl -s -o /dev/null -X DELETE -H "Authorization: token $GT" -H "Content-Type: application/json" -d "{\"sha\":\"$SHA\",\"message\":\"prueba: sin línea base\",\"branch\":\"main\"}" "$G/contents/respaldos/$h.cfg"; done
RS="Red · Respaldar configuración"; DR="Red · Detectar cambios de configuración"
echo "### RESPALDO"
correr "$RS" fw R-C1
T=$(gtxt fw); echo "   fw.cfg: $(echo "$T" | grep -c '^set ') líneas set · ocultos $(echo "$T" | grep -c '<oculto:') · claro-sospechoso $(echo "$T" | grep -cE '\$6\$|\$1\$')"
correr "$RS" rtr-wan-01 R-C2a
T=$(gtxt rtr-wan-01); echo "   rtr-wan-01.cfg: $(echo "$T" | wc -l) líneas · bgp $(echo "$T" | grep -c '^router bgp')"
correr "$RS" sw-core-01 R-C2b
T=$(gtxt sw-core-01); echo "   sw-core-01.cfg: $(echo "$T" | wc -l) líneas · tabla VLAN $(echo "$T" | grep -c 'tabla de VLAN')"
correr "$RS" lb-sede-01 R-C3
T=$(gtxt lb-sede-01); echo "   lb-sede-01.cfg: $(echo "$T" | wc -l) líneas · backend $(echo "$T" | grep -c '^backend') · frontend $(echo "$T" | grep -c '^frontend') · ocultos $(echo "$T" | grep -c '<oculto:')"
N1=$(gcommits fw); correr "$RS" fw R-C4; N2=$(gcommits fw); echo "   commits fw.cfg antes $N1 después $N2"
correr "$RS" suc1 R-C5; echo "   suc1.cfg: $(gtxt suc1 | head -1)"
echo "### DRIFT"
correr "$DR" rtr-wan-01 D-C1
# C2: quitar una línea de la línea base en Git (simula un cambio hecho a mano en el equipo)
python3 - "$GT" <<'PY'
import sys, json, base64, urllib.request
gt = sys.argv[1]; u = "http://127.0.0.1:3000/api/v1/repos/invoke/red-laboratorio/contents/respaldos/rtr-wan-01.cfg"
H = {"Authorization": "token " + gt, "Content-Type": "application/json"}
d = json.load(urllib.request.urlopen(urllib.request.Request(u + "?ref=main", headers=H)))
ls = base64.b64decode(d["content"]).decode().split("\n")
idx = next(i for i, l in enumerate(ls) if l.strip().startswith("neighbor") or l.strip().startswith("ip route") or l.strip().startswith("network"))
print("   línea quitada de la base:", ls[idx].strip()[:80]); del ls[idx]
b = json.dumps({"content": base64.b64encode("\n".join(ls).encode()).decode(), "sha": d["sha"], "message": "prueba drift: línea quitada de la base", "branch": "main"}).encode()
urllib.request.urlopen(urllib.request.Request(u, data=b, headers=H, method="PUT"))
PY
correr "$DR" rtr-wan-01 D-C2
correr "$DR" fw D-C3a
correr "$DR" lb-sede-01 D-C3b
correr "$RS" rtr-wan-01 D-C4-restablecer
correr "$DR" rtr-wan-01 D-C4
correr "$DR" sw-acc-01 D-C5; echo "   sw-acc-01.cfg: $(gtxt sw-acc-01 | head -1)"
echo "TICKETS $(tr '\n' ' ' < /tmp/red-tickets.txt)"
