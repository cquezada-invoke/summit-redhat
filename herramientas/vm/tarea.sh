cd ~/summit-redhat && git pull -q --ff-only origin main; git log -1 --format=%h
bash faceops/version.sh >/dev/null && sudo podman build -q -t localhost/faceops:latest -f faceops/Containerfile faceops >/dev/null && sudo systemctl restart faceops && sleep 25
timeout 900 python3 faceops/aap/configurar.py 2>&1 | grep -i -E "Traceback|error|Grafana de OpenShift" | grep -v -i -E "clave|password|token" | tail -3
source herramientas/pruebas/comun.sh >/dev/null 2>&1; sincronizar
echo "== FaceOps alcanzable desde el clúster"
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
$OC -n openshift-monitoring exec alertmanager-main-0 -c alertmanager -- sh -c 'wget -q -O- --post-data="{}" --header="Content-Type: application/json" http://192.168.122.1:8110/api/alertas/ocp/ocp-sno 2>&1 | head -c 200; echo' 2>&1 | tail -2
echo "== plan Grafana de OpenShift (C1–C6)"
bash herramientas/pruebas/p-grafana-ocp.sh 2>&1 | grep -E "^(CASO|CERT|FIN|EVIDENCIA)"
$OC -n openshift-monitoring get secret alertmanager-main -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d | grep -c "faceops-ocp-"
