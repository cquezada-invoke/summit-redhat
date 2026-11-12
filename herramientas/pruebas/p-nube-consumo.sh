# Plan «Nube · Recuperar consumo y estado» (C1–C5): consumo mensual y estado de la nube GCP del laboratorio, solo lectura,
# con la credencial cifrada de la conexión en AAP. Nunca imprime secretos (la salida del runner es pública).
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Nube · Recuperar consumo y estado"; NUBE="Cloud GCP · invoke-summit-cloud"
sincronizar
# FaceOps se reinicia con cada despliegue de main: si el arnés no responde, espera y repite el caso (hasta 4 veces)
vivo() { for i in $(seq 1 30); do sudo podman exec faceops python3 -c 1 >/dev/null 2>&1 && break; sleep 10; done; sleep 20; }
rec() { local k; for k in 1 2 3 4; do vivo
  caso "$(python3 -c 'import json,sys;print(json.dumps({"auto":sys.argv[1],"params":json.loads(sys.argv[2]),"min":15,"caso":sys.argv[3]}))' "$N" "$1" "$2")"
  case "$RES" in *'sin respuesta del arnés'*) sleep 60 ;; *'"fallido"'*'"fallos": []'*) [ $k -ge 2 ] && return; sleep 60 ;; *) return ;; esac; done; }
res() { jq_ "json.dumps({k: ((v.get('consumo') or {}).get('fuente'), (v.get('consumo') or {}).get('promedio_usd'), (v.get('consumo') or {}).get('mes_actual_usd'), {s: (v.get(s) or {}).get('ok') if (v.get(s) or {}).get('ok') else (v.get(s) or {}).get('falta') for s in ('maquinas','discos','ips','snapshots','firewall','cuotas','claves','recomendaciones','productos','facturacion','presupuesto')}, (v.get('consumo') or {}).get('intentos'), v.get('error')) for k, v in (s.get('faceops_nube_resumen') or {}).items()}, ensure_ascii=False)"; }
vista() { sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -3
import re, html
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app import nube_consumo as N
    c = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); N.recoger(c, forzar=True)
    m = N.muestra(c["id"], 2)
    print("CACHE", {k: m.get(k) for k in ("ok", "fuente", "mes_actual_usd", "promedio_usd", "ts", "falta")} if m else None, "· recursos", len(N.costo_recursos(c["id"], 2)))
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    h = cl.get("/lab/nubes").get_data(as_text=True)
    f = re.search(r'id="infra-2".*?</tr>', h, re.S)
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(f.group(0)))) if f else "sin fila"
    h2 = cl.get("/lab/nubes/2").get_data(as_text=True)
    cons = re.search(r'id="consolidado".*?</table>', h2, re.S)
    print("LISTA", t[-260:])
    print("FICHA", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(cons.group(0))))[:1500] if cons else "sin consolidado " + h2[:200].replace("\n", " "))
Q
}
# C1: recuperar con la credencial de la conexión (ticket «Actualizar ahora»)
rec "{\"nr_nube\":\"$NUBE\"}" C1; E1=$(estado); T1=$(ticket); R1=$(res); echo "C1 $E1 $T1 · $R1"
vivo; V1=$(vista); echo "$V1"
F1=$(echo "$R1" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(next(iter(d.values()))[0] if d else "")' 2>/dev/null)
[ "$E1" = exitoso ] && [ -n "$F1" ] && echo "$V1" | grep -q "^CACHE {'ok'" && echo "$V1" | grep -q "^FICHA" && ! echo "$V1" | grep -q "sin consolidado" && \
  anotar C1 "Recuperar la nube GCP del laboratorio" ok "fuente $F1 · $(echo "$V1" | head -1 | cut -c1-200)" || anotar C1 "Recuperar la nube GCP del laboratorio" falla "$E1 · $R1 · $V1"
# C2: repetir (idempotencia, solo lectura)
rec "{\"nr_nube\":\"$NUBE\"}" C2; E2=$(estado); R2=$(res); echo "C2 $E2 · $R2"
M1=$(echo "$R1" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(json.dumps(next(iter(d.values()))[3]))' 2>/dev/null)
M2=$(echo "$R2" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(json.dumps(next(iter(d.values()))[3]))' 2>/dev/null)
[ "$E2" = exitoso ] && [ "$M1" = "$M2" ] && anotar C2 "Repetir la recuperación" ok "mismas secciones: $M2" || anotar C2 "Repetir la recuperación" falla "$E2 · $M1 → $M2"
# C3: cálculo diario programado (FaceOps lanza la plantilla sin ticket con la credencial de la conexión y lo recoge)
vivo; D3=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -1
import time
from app import create_app
app = create_app()
with app.app_context():
    from app.aap import AAP
    from app.bd import q1
    from app import nube_consumo as N
    c = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    antes = (q1("SELECT ts FROM nube_resumen WHERE cliente_id=? AND infra_id=2", c["id"]) or {}).get("ts")
    jobs = N.lanzar_diario(c, forzar=True, prueba=True)
    aap = AAP.de_cliente(c); st = ""
    for _ in range(90):
        time.sleep(8)
        st = aap.get(f"jobs/{jobs[0]}/")["status"] if jobs else "sin trabajo"
        if st in ("successful", "failed", "error", "canceled", "sin trabajo"):
            break
    cr = [x["name"] for x in aap.get(f"jobs/{jobs[0]}/credentials/").get("results", [])] if jobs else []
    N.recoger(c, forzar=True)
    r = q1("SELECT ts, job FROM nube_resumen WHERE cliente_id=? AND infra_id=2", c["id"])
    print("DIARIO", len(jobs), st, cr, antes, "->", r["ts"] if r else None, r["job"] if r else None, jobs)
Q
)
echo "C3 $D3"
echo "$D3" | grep -q "DIARIO [1-9][0-9]* successful \['GCP · invoke-summit-cloud'\]" && python3 -c "import sys,re;l=sys.argv[1].split();j=re.findall(r'\d+',sys.argv[1].split('[')[-1]);sys.exit(0 if j and sys.argv[1].split('->')[1].split()[1]==j[0] else 1)" "$D3" && \
  anotar C3 "Cálculo diario programado" ok "$D3" || anotar C3 "Cálculo diario programado" falla "$D3"
# C4: datos inválidos (proveedor desconocido): falla en «Datos válidos» sin llamar a la nube
rec "{\"nr_nube\":\"$NUBE\",\"nr_proveedor\":\"oci\"}" C4; E4=$(estado); F4=$(echo "$RES" | grep -o "Datos válidos[^\"]*" | head -1)
echo "C4 $E4 · $F4"
[ "$E4" = fallido ] && [ -n "$F4" ] && anotar C4 "Datos inválidos" ok "fallido en «Datos válidos»" || anotar C4 "Datos inválidos" falla "$E4 · $F4"
# C5: la salida del trabajo de C1 no trae secretos
vivo; S5=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - "$T1" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
app = create_app()
with app.app_context():
    from app.aap import AAP
    from app.bd import q1
    t = q1("SELECT * FROM tickets WHERE numero=?", sys.argv[1]); c = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
    aap = AAP.de_cliente(c)
    txt = aap.s.get(aap.base + f"jobs/{t['aap_job_id']}/stdout/?format=txt", timeout=40).text
    malos = [k for k in ("private_key", "BEGIN PRIVATE", "client_secret", "\"type\": \"service_account\"", "AZURE_SECRET", "AWS_SECRET") if k in txt]
    print("SALIDA", len(txt), "caracteres · secretos:", malos or "ninguno")
Q
)
echo "C5 $S5"
echo "$S5" | grep -q "secretos: ninguno" && ! echo "$S5" | grep -q "SALIDA 0 " && anotar C5 "Salida sin secretos" ok "$S5" || anotar C5 "Salida sin secretos" falla "$S5"
certificar "$N" "Plan consumo de nube · C1–C5 (planes-prueba.yml)"
echo FIN-NUBE-CONSUMO
