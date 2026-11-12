cd ~/summit-redhat && git log --oneline -1 | cut -c1-50
sudo podman exec faceops grep -c subcategoria_red /opt/faceops/app/habilidades.py
sudo podman exec faceops sh -c 'cat /opt/faceops/version* 2>/dev/null | head -2'
set -a; . ~/faceops/faceops.env; set +a
python3 - <<'PY'
import os, ssl, urllib.request, json
cx = ssl._create_unverified_context(); B = os.environ["FACEOPS_AAP_URL"].rstrip("/") + "/api/controller/v2/"
H = {"Authorization": "Bearer " + os.environ["FACEOPS_AAP_TOKEN"]}
for n in ("Red · Respaldar configuración", "Red · Detectar cambios de configuración"):
    r = json.loads(urllib.request.urlopen(urllib.request.Request(B + "job_templates/?name=" + urllib.parse.quote(n), headers=H), context=cx).read())
    print(n, r["count"])
PY
