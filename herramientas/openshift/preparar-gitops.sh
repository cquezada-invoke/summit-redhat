#!/bin/bash
# Segmentos de negocio en OpenShift con GitOps · preparación del clúster y de AAP (idempotente; no imprime secretos).
#  · OpenShift GitOps (Argo CD) desde el catálogo de Red Hat
#  · registro interno de imágenes (laboratorio: emptyDir) para los builds de los componentes
#  · cuenta de servicio faceops-despliegue (en producción: un ClusterRole acotado; en el laboratorio: cluster-admin)
#  · Gitea: usuario de servicio faceops-gitops y repositorio invoke/gitops-segmentos (lo lee Argo CD)
#  · AAP: credenciales «OpenShift · SNO (despliegue)» y «FaceOps · Gitea GitOps»
set -euo pipefail
D=$HOME/openshift/sno; export KUBECONFIG=$D/instalar/auth/kubeconfig; OC=$D/bin/oc
API=https://192.168.122.50:6443
paso() { echo "== $*"; }

paso "Operadores de Red Hat: OpenShift GitOps (Argo CD) y OpenShift Pipelines (Tekton)"
operador() {  # $1 namespace  $2 paquete  $3 canal
  $OC get ns $1 >/dev/null 2>&1 || $OC create ns $1 >/dev/null
  # bundle-unpack-timeout: en el laboratorio la primera descarga del bundle puede tardar más de los 10 min por defecto
  $OC apply -f - >/dev/null <<Y
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata: {name: $1, namespace: $1, annotations: {operatorframework.io/bundle-unpack-timeout: "45m"}}
spec: {upgradeStrategy: Default}
Y
  if $OC -n $1 get sub $2 -o jsonpath='{.status.conditions[*].reason}' 2>/dev/null | grep -q BundleUnpackFailed; then
    $OC -n $1 delete sub $2 >/dev/null; $OC -n $1 delete installplan --all >/dev/null 2>&1 || true
    $OC -n openshift-marketplace delete job -l operatorframework.io/bundle-unpack-ref >/dev/null 2>&1 || true
  fi
  $OC apply -f - >/dev/null <<Y
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata: {name: $2, namespace: $1}
spec: {channel: $3, installPlanApproval: Automatic, name: $2, source: redhat-operators, sourceNamespace: openshift-marketplace}
Y
}
operador openshift-gitops-operator openshift-gitops-operator latest
operador openshift-operators openshift-pipelines-operator-rh latest
for i in $(seq 1 120); do $OC -n openshift-gitops get deploy openshift-gitops-server >/dev/null 2>&1 && $OC get ns openshift-pipelines >/dev/null 2>&1 && break; sleep 15; done
$OC -n openshift-gitops rollout status deploy/openshift-gitops-server --timeout=900s >/dev/null && echo "   · Argo CD listo"
for i in $(seq 1 60); do $OC get crd pipelines.tekton.dev >/dev/null 2>&1 && $OC -n openshift-pipelines get deploy tekton-pipelines-controller >/dev/null 2>&1 && break; sleep 15; done
$OC -n openshift-pipelines rollout status deploy/tekton-pipelines-controller --timeout=900s >/dev/null && echo "   · OpenShift Pipelines listo"

paso "Registro interno de imágenes (emptyDir, laboratorio)"
$OC patch configs.imageregistry.operator.openshift.io cluster --type=merge \
  -p '{"spec":{"managementState":"Managed","storage":{"emptyDir":{}},"replicas":1}}' >/dev/null
for i in $(seq 1 30); do [ "$($OC -n openshift-image-registry get deploy image-registry -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" = "1" ] && break; sleep 10; done
echo "   · registro: $($OC -n openshift-image-registry get deploy image-registry -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)/1"

paso "Cuenta de servicio faceops-despliegue"
$OC get ns faceops >/dev/null 2>&1 || $OC create ns faceops >/dev/null
$OC -n faceops get sa faceops-despliegue >/dev/null 2>&1 || $OC -n faceops create sa faceops-despliegue >/dev/null
$OC adm policy add-cluster-role-to-user cluster-admin -z faceops-despliegue -n faceops >/dev/null
$OC apply -f - >/dev/null <<Y
apiVersion: v1
kind: Secret
metadata: {name: faceops-despliegue-token, namespace: faceops, annotations: {kubernetes.io/service-account.name: faceops-despliegue}}
type: kubernetes.io/service-account-token
Y
for i in $(seq 1 20); do T=$($OC -n faceops get secret faceops-despliegue-token -o jsonpath='{.data.token}' 2>/dev/null | base64 -d); [ -n "$T" ] && break; sleep 2; done

paso "Gitea (repositorio GitOps) y credenciales en AAP"
TOKEN="$T" API="$API" python3 - <<'P'
import base64, glob, json, os, re, secrets, ssl, urllib.error, urllib.parse, urllib.request
SEC = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/demo-drift/cicd-secretos.txt")) if "=" in l)
ADMIN = "Basic " + base64.b64encode(f"{SEC['gitea_usuario']}:{SEC['gitea_clave']}".encode()).decode()  # administrador de Gitea
def gitea(m, p, d=None, auth=None, tolerar=False):
    q = urllib.request.Request(f"http://127.0.0.1:3000/api/v1/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Content-Type": "application/json", "Authorization": auth or ADMIN})
    try:
        with urllib.request.urlopen(q, timeout=30) as r:
            b = r.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        if tolerar: return None
        raise SystemExit(f"Gitea {m} {p}: {e.code} {e.read()[:200]}")
F = os.path.expanduser("~/faceops/gitops-token")
if not gitea("GET", "users/faceops-gitops", tolerar=True):
    gitea("POST", "admin/users", {"username": "faceops-gitops", "email": "faceops-gitops@invoke.lab", "password": secrets.token_urlsafe(24),
                                   "must_change_password": False, "full_name": "FaceOps GitOps (AAP)"})
if not os.path.exists(F):
    pw = secrets.token_urlsafe(24)
    gitea("PATCH", "admin/users/faceops-gitops", {"login_name": "faceops-gitops", "source_id": 0, "password": pw, "must_change_password": False})
    auth = "Basic " + base64.b64encode(f"faceops-gitops:{pw}".encode()).decode()
    tk = gitea("POST", "users/faceops-gitops/tokens", {"name": f"aap-{secrets.token_hex(3)}", "scopes": ["write:repository", "read:user"]}, auth=auth)["sha1"]
    fd = os.open(F, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, tk.encode()); os.close(fd)
if not gitea("GET", "repos/invoke/gitops-segmentos", tolerar=True):
    gitea("POST", "orgs/invoke/repos", {"name": "gitops-segmentos", "private": False, "auto_init": True, "default_branch": "main",
                                         "description": "Estado deseado de los segmentos de negocio en OpenShift (lo genera AAP, lo aplica Argo CD)"})
gitea("PUT", "repos/invoke/gitops-segmentos/collaborators/faceops-gitops", {"permission": "write"})
print("   · Gitea invoke/gitops-segmentos (escribe faceops-gitops, lee Argo CD)")

CL = re.search(r"^gateway_admin_password=(.*)$", open(glob.glob(os.path.expanduser("~/aap/*/inventory-demo"))[0]).read(), re.M).group(1).strip()
AU = "Basic " + base64.b64encode(f"admin:{CL}".encode()).decode(); CTX = ssl._create_unverified_context()
def aap(m, p, d=None):
    q = urllib.request.Request(f"https://127.0.0.1/api/controller/v2/{p}", method=m, data=json.dumps(d).encode() if d is not None else None,
                               headers={"Authorization": AU, "Content-Type": "application/json"})
    with urllib.request.urlopen(q, context=CTX, timeout=30) as r:
        b = r.read(); return json.loads(b) if b else {}
uno = lambda rec, **k: (aap("GET", f"{rec}/?" + urllib.parse.urlencode(k))["results"] or [None])[0]
org = uno("organizations", name="FaceOps")
ct = uno("credential_types", name="OpenShift or Kubernetes API Bearer Token")
cd = {"name": "OpenShift · SNO (despliegue)", "organization": org["id"], "credential_type": ct["id"],
      "description": "Cuenta de servicio faceops-despliegue: crea los namespaces de los segmentos y sus aplicaciones de Argo CD",
      "inputs": {"host": os.environ["API"], "bearer_token": os.environ["TOKEN"], "verify_ssl": False}}
c = uno("credentials", name=cd["name"]); aap("PATCH", f"credentials/{c['id']}/", cd) if c else aap("POST", "credentials/", cd)
cg = uno("credential_types", name="FaceOps · Gitea GitOps") or aap("POST", "credential_types/", {
    "name": "FaceOps · Gitea GitOps", "kind": "cloud", "description": "Usuario de servicio que escribe el estado deseado de los segmentos en Git",
    "inputs": {"fields": [{"id": "url", "label": "URL"}, {"id": "usuario", "label": "Usuario"}, {"id": "token", "label": "Token", "secret": True}],
               "required": ["url", "usuario", "token"]},
    "injectors": {"extra_vars": {"faceops_gitops_url": "{{ url }}", "faceops_gitops_usuario": "{{ usuario }}", "faceops_gitops_token": "{{ token }}"}}})
dg = {"name": "FaceOps · Gitea GitOps", "organization": org["id"], "credential_type": cg["id"],
      "inputs": {"url": "http://192.168.122.1:3000", "usuario": "faceops-gitops", "token": open(F).read().strip()}}
c = uno("credentials", name=dg["name"]); aap("PATCH", f"credentials/{c['id']}/", dg) if c else aap("POST", "credentials/", dg)
print("   · AAP: «OpenShift · SNO (despliegue)» y «FaceOps · Gitea GitOps»")
P
echo "LISTO"
