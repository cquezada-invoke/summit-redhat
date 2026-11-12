cd ~/summit-redhat && git log --oneline -1 | cut -c1-60
echo "== faceops HTTP: $(curl -s -o /dev/null -w '%{http_code}' http://192.168.122.1:8110/sesion/valida)"
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -40
import json
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q, q1, j
    from app.pruebas import estado
    e = q1("SELECT * FROM equipos WHERE id=29")
    print("EQ", e["nombre"], e["categoria"], e["so"], e.get("energia"), e.get("ambiente"))
    d = j(e["datos"], {}); o = d.get("ocp") or {}
    print("REC", o.get("recolectado"), "nodos", len(o.get("nodos") or []), "recursos", o.get("recursos"))
    for n in o.get("nodos") or []: print("NODO", n.get("nombre"), n.get("roles"), n.get("cpu"), n.get("memoria_gb"), n.get("cpu_uso"), n.get("memoria_uso_gb"))
    for x in o.get("operadores") or []: print("OP", json.dumps(x, ensure_ascii=False))
    print("STORAGE", o.get("storage"))
    for n in ("OpenShift · Recolectar clúster", "OpenShift · Operador", "OpenShift · Agente de ACS"):
        a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", e["cliente_id"], n)
        print("AUTO", n, (estado(dict(a))["estado"], a["tipo"], a["usa_limite"], a["categoria"], a["tecnico"][:200]) if a else None)
Q
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
echo "== oc"; $OC get nodes 2>&1 | tail -3; $OC get sub -A 2>&1 | tail -15; $OC get csv -A 2>/dev/null | awk '{print $2,$NF}' | sort -u | head -20
$OC get catalogsource -n openshift-marketplace 2>&1 | tail -6
for p in odf-operator lvms-operator kubevirt-hyperconverged mtv-operator openshift-gitops-operator openshift-pipelines-operator-rh servicemeshoperator3 servicemeshoperator serverless-operator cluster-logging loki-operator cluster-observability-operator netobserv-operator rhacs-operator openshift-cert-manager-operator compliance-operator file-integrity-operator security-profiles-operator advanced-cluster-management quay-operator amq-streams amq-broker-rhel8 amq-broker-rhel9 rhbk-operator datagrid redhat-oadp-operator devspaces web-terminal rhods-operator; do
  $OC get packagemanifest -n openshift-marketplace $p -o jsonpath='{.metadata.name}|{.status.catalogSource}|{.status.defaultChannel}|{range .status.channels[*]}{.name},{end}|{.status.channels[0].currentCSVDesc.installModes}{"\n"}' 2>&1 | head -1 | cut -c1-400
done
echo "== AAP creds"; sudo podman exec -i faceops python3 - <<'Q' 2>&1 | tail -8
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app.aap import AAP
    aap = AAP.de_cliente(q1("SELECT * FROM clientes ORDER BY id LIMIT 1"))
    for c in aap.todos("credentials/", name__startswith="OpenShift"): print("CRED", c["id"], c["name"])
    for h in aap.todos("hosts/", name__icontains="ocp"): print("HOST", h["name"], h["summary_fields"]["inventory"]["name"])
Q
