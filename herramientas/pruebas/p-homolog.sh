# Plan «Homologación · Relevar entorno» (C1–C5)
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Homologación · Relevar entorno"
for m in nodo-jboss-dev nodo-jboss-qa nodo-jboss; do energia $m encender; done
rel() { caso "{\"auto\":\"$N\",\"equipo\":\"$1\",\"params\":{},\"min\":12,\"caso\":\"$2\"}"; }
k() { jq_ "(list((s.get('faceops_entorno') or {}).values()) or [{}])[0].get('$1')"; }
ok1=1; ev1=""; declare -A HUE
for m in nodo-jboss-dev nodo-jboss-qa nodo-jboss; do
  rel $m C1; V=$(k jboss.version); J=$(k jboss.java); D=$(k so.distribucion); HUE[$m]=$(echo "$RES" | python3 -c 'import json,sys;r=json.load(sys.stdin);print(json.dumps((r.get("stats") or {}).get("faceops_entorno"),sort_keys=True))' | sha256sum | cut -c1-10)
  ev1="$ev1 $m:[$D · JBoss $V · Java $J]"; { [ "$(estado)" = exitoso ] && [ "$V" != None ] && [ "$J" != None ]; } || ok1=0
  # C2: contra la máquina
  IP=$(ip_de $m); JV=$(en $IP 'java -version 2>&1 | head -1'); SAL=$(echo "$RES" | grep -ciE 'password="[^"]+|jdbc:[a-z]+://')
  echo "$m · java en máquina: $JV · informe: $J · secretos en salida: $SAL"
  JN=$(echo $J | grep -oE '[0-9]+(\.[0-9]+)*' | head -1)
  [ -n "$JN" ] && echo "$JV" | grep -qF "$JN" && [ "$SAL" = 0 ] || ok2=0
done
[ $ok1 = 1 ] && anotar C1 "Relevar JBoss dev, QA y producción" ok "$ev1" || anotar C1 "Relevar JBoss dev, QA y producción" falla "$ev1"
[ "${ok2:-1}" = 1 ] && anotar C2 "Comparar contra la máquina" ok "Java coincide; sin contraseñas ni URL de conexión" || anotar C2 "Comparar contra la máquina" falla "difiere Java o hay secretos"
# C3: desviación de memoria en QA y vuelta atrás
R0=$(rel nodo-jboss-qa C3a >/dev/null; k recursos.ram_gb)
caso "{\"auto\":\"Infra · Ajustar recursos de máquinas\",\"params\":{\"ir_maquinas\":\"nodo-jboss-qa:1:3072\"},\"min\":20,\"caso\":\"C3-ajuste\"}"; EA=$(estado); TA=$(ticket); sleep 60
rel nodo-jboss-qa C3; R1=$(k recursos.ram_gb)
PAG=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -1
from app import create_app
app = create_app()
from app.bd import q1, q
import re
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    segs = [r["segmento"] for r in q("SELECT DISTINCT segmento FROM arquitecturas WHERE estado='aprobada'")]
    hit = []
    for sg in segs:
        h = c.get("/faceops/mapa", query_string={"segmento": sg, "ambiente": "homologacion"}).get_data(as_text=True)
        if "nodo-jboss-qa" in h:
            fila = re.findall(r"(?is)<tr[^>]*>(?:(?!</tr>).)*?(?:RAM|Memoria|ram)(?:(?!</tr>).)*</tr>", h)
            hit.append(f"{sg}: {len(fila)} filas de memoria; '3 GB' {'sí' if re.search(r'3\s*GB', h) else 'no'}")
    print(" | ".join(hit) or "sin segmento con nodo-jboss-qa")
Q
)
echo "ram QA $R0 → $R1 · homologación: $PAG"
caso "{\"auto\":\"Infra · Ajustar recursos de máquinas\",\"params\":{\"ir_maquinas\":\"nodo-jboss-qa:1:2048\"},\"min\":20,\"caso\":\"C3-revertir\"}"; ER=$(estado); sleep 60
rel nodo-jboss-qa C3b; R2=$(k recursos.ram_gb)
[ "$EA" = exitoso ] && [ "$ER" = exitoso ] && [ "$R1" != "$R0" ] && [ "$R2" = "$R0" ] && echo "$PAG" | grep -q "sí" && anotar C3 "Desviación de memoria en QA" ok "RAM $R0→$R1 GB visible en homologación ($PAG); revertido a $R2 GB ($TA)" || anotar C3 "Desviación de memoria en QA" falla "$EA/$ER RAM $R0→$R1→$R2 · $PAG"
# C4: sin JBoss y con JBoss detenido
energia nodo-storage encender
rel nodo-storage C4a; E4a=$(estado); V4a=$(k jboss.version)
caso "{\"auto\":\"Plataforma · Controlar servicio\",\"equipo\":\"nodo-jboss-dev\",\"params\":{\"sp_unidades\":[\"jboss\",\"jboss-eap\",\"eap7\",\"wildfly\"],\"sp_accion\":\"apagar\",\"sp_plataforma\":\"JBoss\"},\"min\":15,\"caso\":\"C4-detener\"}"; EP=$(estado)
rel nodo-jboss-dev C4b; E4b=$(estado); ES=$(k jboss.estado)
caso "{\"auto\":\"Plataforma · Controlar servicio\",\"equipo\":\"nodo-jboss-dev\",\"params\":{\"sp_unidades\":[\"jboss\",\"jboss-eap\",\"eap7\",\"wildfly\"],\"sp_accion\":\"encender\",\"sp_plataforma\":\"JBoss\"},\"min\":15,\"caso\":\"C4-iniciar\"}"
echo "sin JBoss: $E4a version=$V4a · detenido: $EP/$E4b estado=$ES"
[ "$E4a" = exitoso ] && [ "$V4a" = None ] && [ "$E4b" = exitoso ] && echo "$ES" | grep -qiE "deten|inactiv|stop" && anotar C4 "Sin JBoss y JBoss detenido" ok "nodo-storage solo sistema; nodo-jboss-dev JBoss $ES" || anotar C4 "Sin JBoss y JBoss detenido" falla "$E4a/$V4a · $EP/$E4b/$ES"
# C5: repetir (solo lectura)
rel nodo-jboss C5; H=$(echo "$RES" | python3 -c 'import json,sys;r=json.load(sys.stdin);print(json.dumps((r.get("stats") or {}).get("faceops_entorno"),sort_keys=True))' | sha256sum | cut -c1-10)
[ "$(estado)" = exitoso ] && [ "$H" = "${HUE[nodo-jboss]}" ] && anotar C5 "Repetir" ok "misma huella $H" || anotar C5 "Repetir" falla "$(estado) ${HUE[nodo-jboss]} → $H"
certificar "$N" "Plan homologación · C1–C5 (planes-prueba.yml)"
for m in nodo-jboss-dev nodo-jboss-qa nodo-jboss; do energia $m apagar; done
echo FIN-HOMOLOG
