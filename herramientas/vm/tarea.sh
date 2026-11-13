#!/bin/bash
# Plan «Sistema · Inventariar máquina» (11 casos) y «Sistema · Paquetes · inventario y respaldo» en la VM de prueba de la Etapa 1.
# Tickets reales (prueba=True) aprobados con la regla de Cristian. Sin secretos en la salida.
SHA=8e76c900
for i in $(seq 1 120); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); [ -n "$D" ] && git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done
echo "desplegado: $(cut -c1-8 ~/faceops-autodeploy/desplegado) · $(cut -c1-140 ~/faceops-autodeploy/estado)"
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Sistema · Inventariar máquina"; PQ="Sistema · Paquetes · inventario y respaldo"; M=${M:-srv-e1prueba-01}; W=srv-windows-iis-01
IP=$(ip_de $M); echo "VM de prueba $M $IP · $(sudo virsh domstate $M 2>&1)"
TK=$(sudo podman exec faceops printenv FACEOPS_RESPALDO_TOKEN | tr -d "\r"); G=http://192.168.122.1:3000
git_() { curl -s -H "Authorization: token $TK" "$G/api/v1$1"; }
arbol() { git_ "/repos/repositorio/maquina-$1/git/trees/master?recursive=true&per_page=10000" | python3 -c 'import json,sys;print("\n".join(x["path"] for x in (json.load(sys.stdin).get("tree") or []) if x["type"]=="blob"))'; }
ultimo_commit() { git_ "/repos/repositorio/maquina-$1/commits?sha=master&limit=1&stat=false" | python3 -c 'import json,sys;c=json.load(sys.stdin);c=c[0] if isinstance(c,list) and c else {};print(c.get("sha","")[:8], (c.get("commit") or {}).get("message","").replace("\n"," ")[:200])'; }
# corrida limpia: repositorio y artefactos de la VM de prueba de corridas anteriores fuera (solo de srv-e1prueba-01)
curl -s -o /dev/null -w "repositorio previo borrado: %{http_code}\n" -X DELETE -H "Authorization: token $TK" "$G/api/v1/repos/repositorio/maquina-$M"
for v in $(git_ "/packages/repositorio?type=generic&q=maquina-$M&limit=50" | python3 -c 'import json,sys;print(" ".join(p["version"] for p in json.load(sys.stdin) if p["name"]=="maquina-'$M'"))'); do curl -s -o /dev/null -X DELETE -H "Authorization: token $TK" "$G/api/v1/packages/repositorio/generic/maquina-$M/$v"; echo "artefacto previo borrado $v"; done
git_ "/repos/repositorio/maquina-$M" | grep -q '"id"' && echo "AVISO: $M ya tenía repositorio" || echo "$M sin repositorio propio (C1 válido)"
fopy() { sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - "$@" 2>/dev/null; }
punto() {  # punto <maquina> → código del último punto (lo crea si el inventario cambió algo) y si su ficha trae lo pedido
  fopy "$1" "${2:-}" <<'Q' | grep '^PUNTO'
import sys, json
from app import create_app
app = create_app()
from app.bd import q1
from app import puntos
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); e = dict(q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cl["id"], sys.argv[1]))
p = puntos.crear(cl, e, origen="inventario", usuario="qa.nucleo", nota="Plan de prueba de la Etapa 1", si_cambio=True)
c = app.test_client(); u = q1("SELECT id FROM usuarios WHERE usuario='qa.nucleo'")["id"]
with c.session_transaction() as s: s["uid"] = u; s["csrf"] = "x"; s["cliente_id"] = cl["id"]
h = c.get(f"/lab/inventario/{e['id']}/puntos/{p['codigo']}").get_data(as_text=True)
busca = sys.argv[2]
print("PUNTO", p["codigo"], "configuracion", "4 · Configuración" in h and "si" or "no", "cobertura", "7 · Cobertura" in h and "si" or "no", "cubierto" if "cubierto" in h else "", f"· «{busca}» {'aparece' if busca and busca in h else 'no aparece'}" if busca else "")
Q
}
vault_arch() { fopy "$1" <<'Q' | grep '^BOV'
import sys
from app import create_app
create_app()
from app.boveda import leer
d = leer(f"maquinas/{sys.argv[1]}/archivos") or {}
print("BOV", len(d), sorted(k for k in d if "e1" in k))
Q
}
hostvar() {  # hostvar <maquina> <python_interpreter|-> : fija o quita ansible_python_interpreter en su host de AAP
  fopy "$1" "$2" <<'Q' | grep '^HV'
import sys, json, yaml
from app import create_app
create_app()
from app.bd import q1
from app.aap import AAP
cl = q1("SELECT * FROM clientes WHERE codigo='lab'"); aap = AAP.de_cliente(cl); aap.en_vivo = True
e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cl["id"], sys.argv[1])
inv = (aap.get("inventories/", name=e["inventario"]).get("results") or [{}])[0]["id"]
h = aap.get("hosts/", name=sys.argv[1], inventory=inv)["results"][0]
v = yaml.safe_load(h.get("variables") or "") or {}
if sys.argv[2] == "-": v.pop("ansible_python_interpreter", None)
else: v["ansible_python_interpreter"] = sys.argv[2]
aap.patch(f"hosts/{h['id']}/", {"variables": json.dumps(v)})
print("HV", sys.argv[1], v.get("ansible_python_interpreter", "(sin)"))
Q
}
inv() { caso "{\"auto\":\"$N\",\"equipo\":\"$1\",\"params\":{},\"min\":30,\"caso\":\"$2\"}" > /dev/null; echo "== $2 $1: $(estado) $(ticket)"; }
git_s() { jq_ "{k: {x: y for x, y in v.items() if x in ('CAMBIOS','COMMIT','SECRETOS','SECRETOS_BOVEDA','ARCHIVOS','ERROR')} for k, v in (s.get('faceops_inventario_git') or {}).items()}"; }
arch_s() { jq_ "[x for v in (s.get('faceops_inventario_git') or {}).values() for x in v.get('archivo', []) if x.startswith(('ART_','COB_AUTO','COB_RUTA','COB_SINPAQ','COB_SECRETO'))][:14]"; }
v_() { jq_ "$1"; }
CAMB() { jq_ "list((s.get('faceops_inventario_git') or {}).values())[0].get('CAMBIOS','') if s.get('faceops_inventario_git') else ''"; }

en $IP "sudo rm -rf /data /app-grande /opt/herramienta /opt/app-e1 /opt/jboss-eap-7.4 /etc/pki/tls/private/e1.key; sudo sed -i '/Etapa 1/d' /etc/motd"   # restos de la corrida anterior (C8 y C10 se crean en su caso)
if [ -z "${SALTAR_C12:-}" ]; then
# ---- C1 · RHEL 9 sin repositorio propio
inv $M C1; git_s; A=$(arbol $M); NP=$(jq_ "len(((s.get('faceops_paquetes') or {}).get('$M') or {}).get('paquetes') or [])")
SEC=$(echo "$A" | grep -E "etc/shadow|etc/gshadow|ssh_host_.*key$|/private/|\.key$|\.jks$" | head -3)
MET=$(echo "$A" | grep ^meta/ | sed 's|meta/||' | tr '\n' ' ')
echo "  archivos/etc: $(echo "$A" | grep -c ^archivos/etc/) · meta: $MET · secretos en git: ${SEC:-ninguno} · paquetes $NP"
OK=falla; [ "$(estado)" = exitoso ] && echo "$A" | grep -q ^archivos/etc/hosts && echo "$MET" | grep -q paquetes.txt && echo "$MET" | grep -q servicios.txt && echo "$MET" | grep -q selinux.txt && echo "$MET" | grep -q usuarios.txt && echo "$MET" | grep -q permisos.txt && echo "$MET" | grep -q sistema.txt && [ -z "$SEC" ] && OK=ok
anotar C1 "RHEL 9 sin repositorio propio" $OK "$(ticket): repositorio/maquina-$M rama master con $(echo "$A" | grep -c ^archivos/) archivos y meta ($MET); sin secretos en Git; $NP paquetes"
NP1=$NP
# ---- C2 · repetir sin cambios
inv $M C2; git_s; C=$(CAMB)
OK=falla; [ "$(estado)" = exitoso ] && [ "$C" = 0 ] && OK=ok; anotar C2 "Repetir sin cambios" $OK "$(ticket): CAMBIOS=$C, sin commit"
else NP1=${NP1:-503}; fi
# ---- C3 · cambiar /etc y repetir
en $IP "echo '# Etapa 1 · prueba de inventario $(date +%s)' | sudo tee -a /etc/motd >/dev/null"
inv $M C3; git_s; C=$(CAMB); UC=$(ultimo_commit $M); echo "  último commit: $UC"; PT=$(punto $M "motd"); echo "  $PT"
OK=falla; [ "$(estado)" = exitoso ] && [ "${C:-0}" -ge 1 ] && echo "$UC" | grep -q "1 archivo" && echo "$UC" | grep -q "etc/motd" && echo "$PT" | grep -q "configuracion si" && OK=ok
anotar C3 "Cambiar un archivo de /etc y repetir" $OK "$(ticket): CAMBIOS=$C, commit «$(echo $UC | cut -c1-90)»; $PT"
# ---- C4 · JBoss por RPM (nodo-jboss-dev) y JBoss desde zip en /opt (VM de prueba)
en $IP 'set -e; B=/opt/jboss-eap-7.4/standalone; sudo mkdir -p $B/configuration $B/deployments $B/data/content $B/tmp $B/log /opt/jboss-eap-7.4/bin
 echo "<server xmlns=\"urn:jboss:domain:20.0\"><!-- Etapa 1 --></server>" | sudo tee $B/configuration/standalone.xml >/dev/null
 echo "app de prueba" | sudo tee $B/deployments/app-e1.war >/dev/null; sudo touch $B/deployments/app-e1.war.deployed
 echo dato | sudo tee $B/data/content/x >/dev/null; echo tmp | sudo tee $B/tmp/x >/dev/null; echo log | sudo tee $B/log/server.log >/dev/null
 echo "#!/bin/sh" | sudo tee /opt/jboss-eap-7.4/bin/standalone.conf >/dev/null
 sudo dd if=/dev/urandom of=$B/configuration/grande-e1.bin bs=1M count=30 status=none'
inv $M C4a; git_s; A=$(arbol $M); OM=$(git_ "/repos/repositorio/maquina-$M/raw/meta/omitidos.txt?ref=master" | grep -c grande-e1)
J1=$(echo "$A" | grep -c "opt/jboss-eap-7.4/standalone/configuration/standalone.xml"); J2=$(echo "$A" | grep -c "deployments/app-e1.war"); JX=$(echo "$A" | grep -cE "jboss-eap-7.4/standalone/(data|tmp|log)/|grande-e1")
T1=$(ticket); E1=$(estado)
inv nodo-jboss-dev C4b; git_s; AR=$(arbol nodo-jboss-dev); R1=$(echo "$AR" | grep -c "standalone/configuration/standalone.xml"); RX=$(echo "$AR" | grep -cE "standalone/(data|tmp|log)/")
echo "  zip: configuración $J1 · despliegue $J2 · data/tmp/log o grande en git $JX · omitido $OM | RPM: configuración $R1 · data/tmp/log $RX"
OK=falla; [ "$E1" = exitoso ] && [ "$(estado)" = exitoso ] && [ $J1 -ge 1 ] && [ $J2 -ge 1 ] && [ $JX = 0 ] && [ $OM -ge 1 ] && [ $R1 -ge 1 ] && [ $RX = 0 ] && OK=ok
anotar C4 "JBoss por RPM y desde zip en /opt" $OK "$T1 y $(ticket): standalone/configuration y deployments en archivos/ (zip y RPM); data, tmp y log excluidos; grande-e1.bin (30 MB) en meta/omitidos.txt con su huella"
# ---- C8 · cobertura
en $IP 'set -e; sudo mkdir -p /data/prueba /app-grande /opt/herramienta/bin; echo "dato chico" | sudo tee /data/prueba/a.txt >/dev/null
 sudo dd if=/dev/urandom of=/app-grande/blob.bin bs=1M count=210 status=none; sudo cp /usr/bin/true /opt/herramienta/bin/herramienta; sudo chmod 755 /opt/herramienta/bin/herramienta'
inv $M C8; git_s; arch_s; A=$(arbol $M); PT=$(punto $M "/app-grande"); echo "  $PT"
S8=$(jq_ "' '.join(x for v in (s.get('faceops_inventario_git') or {}).values() for x in v.get('archivo', []) if x.startswith(('ART_','COB_')))")
OK=falla; [ "$(estado)" = exitoso ] && echo "$A" | grep -q "^archivos/data/prueba/a.txt" && echo "$S8" | grep -q "ART_SUBIDO=/app-grande" && echo "$S8" | grep -q "ART_SUBIDO=/opt/herramienta" && echo "$PT" | grep -q "cobertura si" && OK=ok
anotar C8 "Cobertura" $OK "$(ticket): /data/prueba entra sola a Git; /app-grande (210 MB) y /opt/herramienta (sin paquete) subidos al registro maquina-$M con su huella; el punto muestra 7 · Cobertura"
# ---- C9 · repetir sin cambios
inv $M C9; git_s; S9=$(jq_ "' '.join(x for v in (s.get('faceops_inventario_git') or {}).values() for x in v.get('archivo', []) if x.startswith('ART_'))"); SB=$(jq_ "list((s.get('faceops_inventario_git') or {}).values())[0].get('SECRETOS_BOVEDA','')")
OK=falla; [ "$(estado)" = exitoso ] && echo "$S9" | grep -q "ART_OK=/app-grande" && ! echo "$S9" | grep -q ART_SUBIDO && [ "$SB" = igual ] && [ "$(CAMB)" = 0 ] && OK=ok
anotar C9 "Repetir sin cambios" $OK "$(ticket): ART_OK (no vuelve a subir artefactos), bóveda «$SB», CAMBIOS=$(CAMB)"
# ---- C10 · secretos
en $IP 'set -e; sudo mkdir -p /etc/pki/tls/private /opt/app-e1/conf; sudo openssl genrsa -out /etc/pki/tls/private/e1.key 2048 2>/dev/null; sudo dd if=/dev/urandom of=/opt/app-e1/conf/almacen-e1.jks bs=1k count=4 status=none; echo "app=1" | sudo tee /opt/app-e1/conf/app.properties >/dev/null'
inv $M C10; git_s; A=$(arbol $M); SB=$(jq_ "list((s.get('faceops_inventario_git') or {}).values())[0].get('SECRETOS_BOVEDA','')"); BV=$(vault_arch $M); PT=$(punto $M "almacen-e1.jks"); echo "  $BV · $PT"
EN_GIT=$(echo "$A" | grep -cE "e1.key|almacen-e1.jks")
OK=falla; [ "$(estado)" = exitoso ] && [ "$EN_GIT" = 0 ] && [ "$SB" = guardado ] && echo "$BV" | grep -q "almacen-e1.jks" && echo "$BV" | grep -q "e1.key" && echo "$A" | grep -q "opt/app-e1/conf/app.properties" && echo "$PT" | grep -q cubierto && OK=ok
anotar C10 "Secretos" $OK "$(ticket): e1.key y almacen-e1.jks fuera de Git; en la bóveda maquinas/$M/archivos ($SB); cobertura «cubierto»"
# ---- C5 · sin Python utilizable (camino de RHEL 5/6)
hostvar $M /usr/bin/python-inexistente-e1
en $IP "echo '# Etapa 1 · camino legado $(date +%s)' | sudo tee -a /etc/motd >/dev/null"
inv $M C5; git_s; C=$(CAMB); LG=$(jq_ "str(s.get('faceops_legado') or '')[:60]"); UC=$(ultimo_commit $M)
SAL=$(sudo podman exec -i -e FACEOPS_VIGIA=0 -w /opt/faceops -e PYTHONPATH=/opt/faceops faceops python3 - "$(ticket)" 2>/dev/null <<'Q' | tail -3
import sys, re
from app import create_app
create_app()
from app.bd import q1
from app.aap import AAP
t = q1("SELECT * FROM tickets WHERE numero=?", sys.argv[1]); cl = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"]); aap = AAP.de_cliente(cl); aap.en_vivo = True
ids = [x["job"] for x in aap.todos(f"workflow_jobs/{t['aap_job_id']}/workflow_nodes/") if x.get("job")] if t["aap_job_tipo"] == "workflow" else [t["aap_job_id"]]
txt = ""
for i in ids:
    try: txt += aap.s.get(aap.base + f"jobs/{i}/stdout/", params={"format": "txt"}, timeout=30).text
    except Exception: pass
print("LEGADO", "Datos del sistema por shell" in txt and "Datos para FaceOps (mismos nombres" in txt, "BASE64", "Equipo legado (Python antiguo) · por la salida estándar" in txt and "base64 al controlador" in txt)
Q
)
hostvar $M -
echo "  $SAL · commit $UC"
OK=falla; [ "$(estado)" = exitoso ] && echo "$SAL" | grep -q "LEGADO True BASE64 True" && [ "${C:-0}" -ge 1 ] && echo "$UC" | grep -q "1 archivo" && echo "$UC" | grep -q etc/motd && OK=ok
anotar C5 "Sin Python utilizable (camino RHEL 5/6)" $OK "$(ticket): datos por shell (Equipo legado) e instantánea por la salida estándar (base64); commit con solo etc/motd (lo demás igual que en RHEL 9)"
# ---- C7 · datos del sistema y paquetes (partes 1 y 2) de nuevo normales
inv $M C7; git_s; NP=$(jq_ "len(((s.get('faceops_paquetes') or {}).get('$M') or {}).get('paquetes') or [])"); LG=$(jq_ "str(s.get('faceops_legado') or '')")
FA=$(fopy $M <<'Q' | grep '^FA'
import sys
from app import create_app
create_app()
from app.bd import q1
e = q1("SELECT so, estado, ip FROM equipos WHERE nombre=? AND estado!='retirado'", sys.argv[1]); print("FA", e["so"], "|", e["estado"], "|", e["ip"])
Q
)
echo "  paquetes $NP (C1: $NP1) · $FA"
OK=falla; [ "$(estado)" = exitoso ] && [ "$NP" -ge "$NP1" ] && [ "$NP" -le $((NP1+2)) ] && echo "$FA" | grep -qi "redhat 9\|red hat.*9\|RedHat 9" && OK=ok
anotar C7 "Datos del sistema y paquetes" $OK "$(ticket): facts del sistema ($FA) y $NP paquetes con su fuente (C1: $NP1), igual que antes"
# ---- C6 · Windows con IIS
if existe $W; then
  caso "{\"auto\":\"IIS · Preparar servidor\",\"equipo\":\"$W\",\"params\":{\"iis_sitio\":\"Default Web Site\",\"iis_https\":\"true\"},\"min\":40,\"caso\":\"preparación de C6\"}" > /dev/null; echo "IIS en $W (preparación de C6): $(estado) $(ticket)"
  inv $W C6; git_s; AW=$(arbol $W)
  for f in meta/sitios-iis.txt meta/pools-iis.txt meta/paquetes.txt meta/caracteristicas.txt meta/servicios.txt meta/tareas.txt; do echo "$AW" | grep -qx "$f" && echo "  $f sí" || echo "  $f NO"; done
  AH=$(echo "$AW" | grep -ci "inetsrv/config/applicationHost.config"); echo "  applicationHost.config $AH"
  OK=falla; [ "$(estado)" = exitoso ] && [ "$AH" -ge 1 ] && echo "$AW" | grep -qx meta/sitios-iis.txt && echo "$AW" | grep -qx meta/caracteristicas.txt && echo "$AW" | grep -qx meta/tareas.txt && OK=ok
  anotar C6 "Windows con IIS" $OK "$(ticket): applicationHost.config, sitios y pools, programas, características, servicios y tareas en repositorio/maquina-$W"
else anotar C6 "Windows con IIS" falla "$W no está en el inventario"; fi
echo "CASOS_INV $CASOS"
echo "TKS_INV $TKS"
