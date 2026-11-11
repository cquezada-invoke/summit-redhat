#!/bin/bash
# Plan de prueba de «Data lake · Derivar segmento» (C1–C4 espejo en App de pagos; C5–C8 modo declarada en Banca · Core bancario, límite: la máquina de control del lake).
source ~/summit-redhat/herramientas/pruebas/comun.sh
caso() {  # con stderr a la vista y un reintento si AAP falla en 0 s (actualización del proyecto concurrente)
  for intento in 1 2; do
    sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "$1" < $P/lanzar.py > /tmp/lz.out 2>/tmp/lz.err
    RES=$(grep '^RES ' /tmp/lz.out | tail -1 | cut -c5-)
    [ -z "$RES" ] && { RES='{"estado":"sin respuesta del arnés"}'; echo "ERR $(grep -v -i warn /tmp/lz.err | tail -4 | tr '\n' ' ' | cut -c1-600)"; }
    if echo "$RES" | grep -q 'ejecución 0 s'; then echo "REINTENTO (AAP falló en 0 s: actualización del proyecto)"; sleep 20; continue; fi
    if echo "$RES" | grep -q 'sin respuesta'; then echo "REINTENTO (el contenedor de FaceOps se reinició durante el caso)"; for i in $(seq 1 40); do sudo podman exec faceops true 2>/dev/null && break; sleep 5; done; sleep 15; continue; fi
    break
  done
  echo "RES $RES"
}
A="Data lake · Derivar segmento"
py() { sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - "$@" 2>/dev/null; }
params() {  # params <segmento> <punto> <accion> → JSON de variables del ticket
  py "$1" "$2" "$3" <<'Q' | tail -1
import sys, json
from app import create_app
create_app()
from app.topologia import params_derivacion
print(json.dumps(params_derivacion(1, sys.argv[1], sys.argv[2], sys.argv[3]), ensure_ascii=False))
Q
}
bif() {  # bif <segmento> → "activa|destino|por|n_filas"
  py "$1" <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q
r = q("SELECT * FROM bifurcaciones WHERE cliente_id=1 AND segmento=?", sys.argv[1])
print(f"{r[0]['activa']}|{r[0]['destino']}|{r[0]['por']}|{len(r)}" if r else "none|||0")
Q
}
mapa() {  # mapa <segmento> → "lakes=<n> bif=<n> uso=<json>"
  py "$1" <<'Q' | tail -1
import sys, json, requests
from app import create_app
from app.bd import q1
a = create_app(); u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'"); c = a.test_client()
with c.session_transaction() as s: s["uid"] = u["id"]
n = requests.utils.quote(sys.argv[1])
r = c.get(f"/lab/mapa/datos?vista=negocio&nivel=segmento&valor={n}&ambiente=produccion"); els = r.get_json()["elementos"]
lk = [e for e in els if not e["data"].get("source") and ("lake" in e["data"]["id"])]
bf = [e for e in els if e["data"].get("source") and (e["data"].get("estilo") == "bifurcacion")]
c.get(f"/lab/mapa/lake-uso?segmento={n}"); import time; time.sleep(4)
x = c.get(f"/lab/mapa/lake-uso?segmento={n}")
print(f"http={r.status_code} lakes={len(lk)} bif={len(bf)} puertos={[e['data'].get('puertos') for e in bf]} uso={x.status_code}:{json.dumps(x.get_json(), ensure_ascii=False)[:200]}")
Q
}
NG=192.168.122.11; CO=http://192.168.122.50:30480
lanza() {  # lanza <equipo> <caso>: arma el JSON del caso con python (los parámetros traen comillas y cuerpos JSON)
  for i in $(seq 1 30); do sudo podman exec faceops true 2>/dev/null && break; sleep 5; done
  caso "$(python3 -c 'import json,sys;print(json.dumps({"auto":sys.argv[1],"equipo":sys.argv[2],"params":json.loads(sys.argv[3]),"min":15,"caso":sys.argv[4]},ensure_ascii=False))' "$A" "$1" "$PR" "$2")"
}
# ---- C1 activar espejo en App de pagos
PR=$(params "App de pagos" nodo-nginx activar)
lanza nodo-nginx C1
T=$(en $NG 'sudo nginx -t 2>&1 | tail -1'); B=$(bif "App de pagos")
[ "$(estado)" = exitoso ] && echo "$T" | grep -q successful && [ "${B%%|*}" = 1 ] && anotar C1 "Activar espejo App de pagos" ok "$(ticket) · $T · bif $B" || anotar C1 "Activar espejo App de pagos" falla "$(estado) · $T · $B"
# ---- C2 datos en el lake
sleep 20; E=$(curl -s $CO/estado/app-de-pagos); D=$(curl -s -o /tmp/d.txt -w '%{http_code}' $CO/datos/app-de-pagos); CK=$(grep -ci 'cookie\|authorization' /tmp/d.txt)
echo "$E" | python3 -c 'import json,sys;x=json.load(sys.stdin);sys.exit(0 if x.get("en_s3_archivos",0)>0 and x.get("recibidos",0)>0 else 1)' && [ "$D" = 200 ] && [ "$CK" = 0 ] \
  && anotar C2 "Flujos y datos en el lake" ok "estado ${E:0:200} · datos $D sin cookies" || anotar C2 "Flujos y datos en el lake" falla "${E:0:200} · $D · cookies $CK"
# ---- C3 desactivar
PR=$(params "App de pagos" nodo-nginx desactivar)
lanza nodo-nginx C3
F=$(en $NG 'ls /etc/nginx/default.d/fo-lake-app-de-pagos.conf 2>/dev/null | wc -l'); H=$(curl -sk -o /dev/null -w '%{http_code}' https://$NG/app-jboss/); B=$(bif "App de pagos")
[ "$(estado)" = exitoso ] && [ "$F" = 0 ] && [ "$H" = 200 ] && [ "${B%%|*}" = 0 ] && anotar C3 "Desactivar" ok "$(ticket) · sin archivo · app $H · bif $B" || anotar C3 "Desactivar" falla "$(estado) · archivo $F · app $H · $B"
# ---- C4 repetir la activación (dos veces): sin duplicar
PR=$(params "App de pagos" nodo-nginx activar)
lanza nodo-nginx C4a; T1=$(ticket)
lanza nodo-nginx C4
N=$(en $NG 'sudo nginx -T 2>/dev/null | grep -c "mirror /fo_lake_app_de_pagos;"'); B=$(bif "App de pagos")
[ "$(estado)" = exitoso ] && [ "$N" = 1 ] && [ "${B%%|*}" = 1 ] && anotar C4 "Repetir la activación" ok "$T1 + $(ticket) · 1 mirror · bif $B" || anotar C4 "Repetir la activación" falla "$(estado) · mirrors $N · $B"
# ---- C5 modo declarada: activar Banca · Core bancario
S="Banca · Core bancario"
PR=$(params "$S" nodo-nginx activar)
lanza nodo-nginx C5
B=$(bif "$S"); M=$(mapa "$S"); echo "  $M"
[ "$(estado)" = exitoso ] && [ "${B%%|*}" = 1 ] && echo "$B" | grep -q "apps/banca_core" && echo "$M" | grep -q "lakes=1 bif=1" && echo "$M" | grep -q '"directa": true' \
  && anotar C5 "Declarada: activar Core bancario" ok "$(ticket) · bif $B · $M" || anotar C5 "Declarada: activar Core bancario" falla "$(estado) · $B · $M · $(jq_ 'r.get(\"detalle\",\"\")')"
# ---- C6 repetir
lanza nodo-nginx C6; B=$(bif "$S")
[ "$(estado)" = exitoso ] && [ "${B%%|*}" = 1 ] && [ "${B##*|}" = 1 ] && anotar C6 "Declarada: repetir" ok "$(ticket) · bif $B" || anotar C6 "Declarada: repetir" falla "$(estado) · $B"
# ---- C7 desactivar
PR=$(params "$S" nodo-nginx desactivar)
lanza nodo-nginx C7; B=$(bif "$S"); M=$(mapa "$S"); H=$(curl -sk -o /dev/null -w '%{http_code}' https://banca.sno.34.176.42.56.sslip.io/salud)
[ "$(estado)" = exitoso ] && [ "${B%%|*}" = 0 ] && echo "$M" | grep -q "lakes=0 bif=0" && [ "$H" = 200 ] && anotar C7 "Declarada: desactivar" ok "$(ticket) · bif $B · $M · app $H" || anotar C7 "Declarada: desactivar" falla "$(estado) · $B · $M · $H"
# ---- C8 URL de medición inválida
PR=$(params "$S" nodo-nginx activar | python3 -c 'import json,sys;p=json.load(sys.stdin);p["dl_medicion_url"]="http://no-valida";print(json.dumps(p,ensure_ascii=False))')
lanza nodo-nginx C8; B=$(bif "$S")
[ "$(estado)" = fallido ] && [ "${B%%|*}" = 0 ] && anotar C8 "Declarada: URL inválida" ok "$(ticket) · falla controlada · bif $B" || anotar C8 "Declarada: URL inválida" falla "$(estado) · $B"
certificar "$A" "C1–C8: espejo en App de pagos (activar, datos, desactivar, repetir) y modo declarada en Banca · Core bancario (activar, repetir, desactivar, URL inválida)"
