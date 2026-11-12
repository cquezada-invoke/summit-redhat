# Planes «OpenShift · Recolectar clúster», «OpenShift · Instalar operador» y «OpenShift · Desinstalar operador» sobre ocp-sno.
# Uso: bash p-operadores.sh recolectar | operadores   (operadores = instalar C1–C6 y desinstalar D1–D5, deja el clúster como estaba)
# Requiere ocp-sno encendido (no lo enciende).
source ~/summit-redhat/herramientas/pruebas/comun.sh
CL=ocp-sno
fo() {  # fo '<python>' : evalúa dentro de FaceOps con e = ocp-sno y d = e.datos
  sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - "$1" 2>&1 <<'Q' | grep -v -i -E "warn" | tail -1
import sys, json
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q, q1, j
    e = dict(q1("SELECT * FROM equipos WHERE nombre='ocp-sno' AND estado!='retirado'")); d = j(e["datos"], {}); o = d.get("ocp") or {}
    print(eval(sys.argv[1]))
Q
}
snap() {
  $OC get sub -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.spec.name}{"\n"}{end}' | sort
  $OC get csv -A -l '!olm.copiedFrom' -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' | sort
  $OC get ns -o name | sort; $OC get operators.operators.coreos.com -o name | sort; echo "crd $($OC get crd -o name | wc -l)"
}

if [ "$1" = recolectar ]; then
  N="OpenShift · Recolectar clúster"; CASOS='[]'; TKS='[]'
  caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{},\"min\":15,\"caso\":\"C1\"}" >/dev/null; E1=$(estado)
  R1=$(fo '(len(o.get("operadores") or []), sum(1 for x in o.get("operadores") or [] if "pods" in x), len(o.get("paquetes") or {}), o.get("paquetes_completo"), sorted((x["n"], x.get("pods_listos"), x.get("pods")) for x in o.get("operadores") or []))')
  echo "R1 $R1"
  OK1=$(fo '1 if o.get("operadores") and all("pods" in x for x in o["operadores"]) and len(o.get("paquetes") or {}) >= 20 and str(o.get("paquetes_completo")) == "True" else 0')
  [ "$E1" = exitoso ] && [ "$OK1" = 1 ] && anotar C1 "Recolectar $CL" ok "$R1" || anotar C1 "Recolectar $CL" falla "$E1 $R1 $(jq_ "(r.get('detalle') or '')[:200]")"
  SUBS=$($OC get sub -A -o jsonpath='{range .items[*]}{.spec.name}={.status.installedCSV}{"\n"}{end}' | sort | tr '\n' ' ')
  FO_SUBS=$(fo '" ".join(sorted(x["n"] + "=" + (x.get("csv") or "") for x in o.get("operadores") or [])) + " "')
  PM=$(for p in $(fo '" ".join(sorted(o.get("paquetes") or {}))'); do $OC get packagemanifest -n openshift-marketplace $p -o jsonpath='{.metadata.name}={.status.defaultChannel} ' 2>/dev/null; done)
  FO_PM=$(fo '" ".join(k + "=" + v.get("canal", "") for k, v in sorted((o.get("paquetes") or {}).items())) + " "')
  FASES=$($OC get csv -A -l '!olm.copiedFrom' --no-headers 2>/dev/null | grep -v packageserver | awk '{print $NF}' | sort | uniq -c | tr -s ' ' | tr '\n' ' ')
  [ "$SUBS" = "$FO_SUBS" ] && [ "$PM" = "$FO_PM" ] && anotar C2 "Coincide con el clúster" ok "subs: $SUBS · paquetes: $(echo $PM | wc -w) con su canal por defecto · fases CSV: $FASES" \
    || anotar C2 "Coincide con el clúster" falla "oc=[$SUBS] fo=[$FO_SUBS] pm_oc=[$PM] pm_fo=[$FO_PM]"
  caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{},\"min\":15,\"caso\":\"C3\",\"cambios\":true}" >/dev/null; E3=$(estado); CH3=$(jq_ 'r.get("changed")')
  R3=$(fo '(len(o.get("operadores") or []), len(o.get("paquetes") or {}))')
  [ "$E3" = exitoso ] && [ "$CH3" = 0 ] && anotar C3 "Repetir" ok "changed=$CH3; operadores y paquetes $R3" || anotar C3 "Repetir" falla "$E3 changed=$CH3 $R3"
  echo "EVIDENCIA $CASOS"
  certificar "$N" "Plan Recolectar clúster · C1–C3 (planes-prueba.yml)"
  echo FIN-RECOLECTAR
  exit 0
fi

# ── Instalar operador ──
ANTES=$(snap)
N="OpenShift · Instalar operador"; CASOS='[]'; TKS='[]'
FIO='"oi_paquete":"file-integrity-operator","oi_ns":"openshift-file-integrity","oi_modo":"OwnNamespace","oi_canal":"stable","oi_nombre":"File Integrity"'
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{$FIO},\"min\":25,\"caso\":\"C1\"}" >/dev/null; E1=$(estado)
NS1=$($OC get ns openshift-file-integrity -o jsonpath='{.metadata.labels.faceops\.invoke\.cl/operador}' 2>/dev/null)
OG1=$($OC get og -n openshift-file-integrity -o jsonpath='{.items[0].spec.targetNamespaces}' 2>/dev/null)
SU1=$($OC get sub -n openshift-file-integrity file-integrity-operator -o jsonpath='{.spec.installPlanApproval}/{.status.installedCSV}' 2>/dev/null)
CS1=$($OC get csv -n openshift-file-integrity -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
PO1=$($OC get pods -n openshift-file-integrity --no-headers 2>/dev/null | awk '{print $1":"$3}' | tr '\n' ' ')
FI1=$(fo '[(x.get("fase"), x.get("pods_listos"), x.get("pods"), x.get("ticket")) for x in o.get("operadores") or [] if x["n"] == "file-integrity-operator"]')
[ "$E1" = exitoso ] && [ "$NS1" = file-integrity-operator ] && [ "$OG1" = '["openshift-file-integrity"]' ] && [ "${SU1%%/*}" = Manual ] && [ "$CS1" = Succeeded ] && echo "$PO1" | grep -q Running && echo "$FI1" | grep -q Succeeded \
  && anotar C1 "Instalar File Integrity (OwnNamespace)" ok "ns etiquetado; OG $OG1; sub $SU1; CSV $CS1; pods $PO1; ficha $FI1" \
  || anotar C1 "Instalar File Integrity (OwnNamespace)" falla "$E1 ns=$NS1 og=$OG1 sub=$SU1 csv=$CS1 pods=$PO1 ficha=$FI1 $(jq_ "(r.get('detalle') or '')[:300]")"
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{$FIO},\"min\":20,\"caso\":\"C2\",\"cambios\":true}" >/dev/null; E2=$(estado); CH2=$(jq_ 'r.get("changed")'); YA2=$(jq_ '(s.get("faceops_operador") or {}).get("ya_estaba")')
NSU2=$($OC get sub -A --no-headers 2>/dev/null | grep -c file-integrity-operator)
[ "$E2" = exitoso ] && [ "$CH2" = 0 ] && [ "$YA2" = True ] && [ "$NSU2" = 1 ] && anotar C2 "Repetir File Integrity" ok "changed=0; ya_estaba=True; 1 Subscription" || anotar C2 "Repetir File Integrity" falla "$E2 changed=$CH2 ya=$YA2 subs=$NSU2 $(jq_ "(r.get('detalle') or '')[:200]")"
NO='"oi_paquete":"netobserv-operator","oi_ns":"openshift-netobserv-operator","oi_modo":"AllNamespaces","oi_canal":"stable","oi_nombre":"Network Observability"'
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{$NO},\"min\":25,\"caso\":\"C3\"}" >/dev/null; E3=$(estado)
OG3=$($OC get og -n openshift-netobserv-operator -o jsonpath='{.items[0].spec.targetNamespaces}' 2>/dev/null)
CS3=$($OC get csv -n openshift-netobserv-operator -l '!olm.copiedFrom' -o jsonpath='{.items[0].status.phase}' 2>/dev/null)
PO3=$($OC get pods -n openshift-netobserv-operator --no-headers 2>/dev/null | awk '{print $1":"$3}' | tr '\n' ' ')
[ "$E3" = exitoso ] && [ -z "$OG3" ] && [ "$CS3" = Succeeded ] && echo "$PO3" | grep -q Running && anotar C3 "Instalar Network Observability (AllNamespaces)" ok "OG global; CSV $CS3; pods $PO3" \
  || anotar C3 "Instalar Network Observability (AllNamespaces)" falla "$E3 og=[$OG3] csv=$CS3 pods=$PO3 $(jq_ "(r.get('detalle') or '')[:300]")"
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{\"oi_paquete\":\"no-existe-operator\",\"oi_ns\":\"faceops-prueba-noexiste\",\"oi_modo\":\"OwnNamespace\"},\"min\":15,\"caso\":\"C4\"}" >/dev/null; E4=$(estado); D4=$(jq_ "(r.get('detalle') or '')[:200]")
NS4=$($OC get ns faceops-prueba-noexiste -o name 2>/dev/null)
[ "$E4" = fallido ] && echo "$D4" | grep -q "no existe en el catálogo" && [ -z "$NS4" ] && anotar C4 "Paquete inexistente" ok "$D4; sin namespace" || anotar C4 "Paquete inexistente" falla "$E4 $D4 ns=$NS4"
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{\"oi_paquete\":\"web-terminal\",\"oi_ns\":\"faceops-prueba-wt\",\"oi_modo\":\"OwnNamespace\"},\"min\":15,\"caso\":\"C5\"}" >/dev/null; E5=$(estado); D5=$(jq_ "(r.get('detalle') or '')[:200]")
NS5=$($OC get ns faceops-prueba-wt -o name 2>/dev/null)
[ "$E5" = fallido ] && echo "$D5" | grep -q "no admite el modo" && [ -z "$NS5" ] && anotar C5 "Modo no soportado" ok "$D5; sin namespace" || anotar C5 "Modo no soportado" falla "$E5 $D5 ns=$NS5"
C6=$(sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>/dev/null <<'Q' | tail -1
from app import create_app
app = create_app()
from app.bd import q1
e = q1("SELECT id, cliente_id FROM equipos WHERE nombre='ocp-sno' AND estado!='retirado'")
c = app.test_client()
with c.session_transaction() as s:
    s["uid"] = 1; s["csrf"] = "x"; s["cliente_id"] = e["cliente_id"]
with app.test_request_context():
    from flask import url_for
    U_EQ = url_for("consola.equipo", eid=e["id"]); U_OI = url_for("consola.operador_instalar", eid=e["id"])
antes = q1("SELECT MAX(id) m FROM tickets")["m"]
h = c.get(U_EQ, headers={"Accept": "text/html"}, follow_redirects=True).get_data(as_text=True)
i = h.find("OpenShift Data Foundation"); bloque = h[i:i + 900]
r = c.post(U_OI, data={"_csrf": "x", "paquete": "odf-operator"})
print(r.status_code, "|", (r.get_json() or {}).get("error"), "|", "disabled" in bloque and "3 nodos" in bloque, "|", q1("SELECT MAX(id) m FROM tickets")["m"] == antes)
Q
)
echo "$C6" | grep -q "^400 .*3 nodos.*| True | True" && anotar C6 "ODF en un solo nodo desde la ficha" ok "$C6" || anotar C6 "ODF en un solo nodo desde la ficha" falla "$C6"
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan Instalar operador · C1–C6 (planes-prueba.yml)"

# ── Desinstalar operador ──
N="OpenShift · Desinstalar operador"; CASOS='[]'; TKS='[]'
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{\"od_paquete\":\"file-integrity-operator\",\"od_ns\":\"openshift-file-integrity\"},\"min\":20,\"caso\":\"D1\"}" >/dev/null; E1=$(estado)
R1="ns=$($OC get ns openshift-file-integrity -o name 2>/dev/null) sub=$($OC get sub -A --no-headers 2>/dev/null | grep -c file-integrity) csv=$($OC get csv -A --no-headers 2>/dev/null | grep -c file-integrity) crd=$($OC get crd -o name | grep -c fileintegrit)"
FI1=$(fo '[x["n"] for x in o.get("operadores") or [] if x["n"] == "file-integrity-operator"]')
[ "$E1" = exitoso ] && [ "$R1" = "ns= sub=0 csv=0 crd=0" ] && [ "$FI1" = "[]" ] && anotar D1 "Desinstalar File Integrity" ok "$R1; ficha sin el operador · $(jq_ 's.get("faceops_operador")')" || anotar D1 "Desinstalar File Integrity" falla "$E1 $R1 ficha=$FI1 $(jq_ "(r.get('detalle') or '')[:300]")"
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{\"od_paquete\":\"netobserv-operator\",\"od_ns\":\"openshift-netobserv-operator\"},\"min\":20,\"caso\":\"D2\"}" >/dev/null; E2=$(estado)
R2="ns=$($OC get ns openshift-netobserv-operator -o name 2>/dev/null) sub=$($OC get sub -A --no-headers 2>/dev/null | grep -c netobserv) csv=$($OC get csv -A --no-headers 2>/dev/null | grep -c netobserv) crd=$($OC get crd -o name | grep -c netobserv)"
[ "$E2" = exitoso ] && [ "$R2" = "ns= sub=0 csv=0 crd=0" ] && anotar D2 "Desinstalar Network Observability" ok "$R2" || anotar D2 "Desinstalar Network Observability" falla "$E2 $R2 $(jq_ "(r.get('detalle') or '')[:300]")"
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{\"od_paquete\":\"file-integrity-operator\",\"od_ns\":\"openshift-file-integrity\"},\"min\":15,\"caso\":\"D3\",\"cambios\":true}" >/dev/null; E3=$(estado); CH3=$(jq_ 'r.get("changed")'); ES3=$(jq_ '(s.get("faceops_operador") or {}).get("estaba")')
[ "$E3" = exitoso ] && [ "$CH3" = 0 ] && [ "$ES3" = False ] && anotar D3 "Repetir" ok "changed=0; no estaba instalado" || anotar D3 "Repetir" falla "$E3 changed=$CH3 estaba=$ES3"
caso "{\"auto\":\"$N\",\"equipo\":\"$CL\",\"directo\":true,\"params\":{\"od_paquete\":\"file-integrity-operator\",\"od_ns\":\"kube-system\"},\"min\":15,\"caso\":\"D4\"}" >/dev/null; E4=$(estado); D4=$(jq_ "(r.get('detalle') or '')[:200]")
[ "$E4" = fallido ] && echo "$D4" | grep -q "no uno del sistema" && anotar D4 "Namespace del sistema" ok "$D4" || anotar D4 "Namespace del sistema" falla "$E4 $D4"
DESPUES=$(snap)
if [ "$ANTES" = "$DESPUES" ]; then anotar D5 "El clúster queda como estaba" ok "mismas Subscriptions, CSV, namespaces y $(echo "$DESPUES" | tail -1)"
else anotar D5 "El clúster queda como estaba" falla "$(diff <(echo "$ANTES") <(echo "$DESPUES") | head -8 | tr '\n' ' ')"; fi
echo "EVIDENCIA $CASOS"
certificar "$N" "Plan Desinstalar operador · D1–D5 (planes-prueba.yml)"
echo FIN-OPERADORES
