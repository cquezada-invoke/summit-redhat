#!/bin/bash
# Despliega el data lake de la demo en el SNO (namespace propio). Las claves de MinIO se generan aquí y quedan en Secrets.
set -euo pipefail
export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc; D=$(cd "$(dirname "$0")" && pwd)
$OC create namespace demo-datalake --dry-run=client -o yaml | $OC apply -f - >/dev/null
if ! $OC -n demo-datalake get secret minio >/dev/null 2>&1; then
  U=lake$(openssl rand -hex 3); P=$(openssl rand -base64 24 | tr -d '/+=')
  $OC -n demo-datalake create secret generic minio --from-literal=MINIO_ROOT_USER=$U --from-literal=MINIO_ROOT_PASSWORD=$P >/dev/null
fi
if ! $OC -n demo-datalake get secret trino-catalogo -o jsonpath='{.data.lake\.properties}' | base64 -d | grep -q 'file:///'; then
  # metastore en el disco de Trino (escritura atómica); los datos, en S3
  U=$($OC -n demo-datalake get secret minio -o jsonpath='{.data.MINIO_ROOT_USER}' | base64 -d); P=$($OC -n demo-datalake get secret minio -o jsonpath='{.data.MINIO_ROOT_PASSWORD}' | base64 -d)
  T=$(mktemp); chmod 600 $T
  printf 'connector.name=hive\nhive.metastore=file\nhive.metastore.catalog.dir=file:///data/trino/metastore\nhive.non-managed-table-writes-enabled=true\nfs.hadoop.enabled=true\nfs.native-s3.enabled=true\ns3.endpoint=http://minio:9000\ns3.region=us-east-1\ns3.path-style-access=true\ns3.aws-access-key=%s\ns3.aws-secret-key=%s\n' "$U" "$P" > $T
  $OC -n demo-datalake create secret generic trino-catalogo --from-file=lake.properties=$T --dry-run=client -o yaml | $OC apply -f - >/dev/null; rm -f $T
  $OC -n demo-datalake rollout restart deploy/trino >/dev/null 2>&1 || true
fi
$OC apply -f $D/lake.yaml >/dev/null
$OC -n demo-datalake create configmap carga --from-file=$D/cargar.py --from-file=${DATOS:?ruta de demo-ia-analitica/app/datos.py} --dry-run=client -o yaml | $OC apply -f - >/dev/null
$OC -n demo-datalake rollout status deploy/minio --timeout=300s
$OC -n demo-datalake rollout status deploy/trino --timeout=400s
$OC -n demo-datalake delete job carga --ignore-not-found >/dev/null; $OC apply -f $D/carga-job.yaml >/dev/null
$OC -n demo-datalake wait --for=condition=complete job/carga --timeout=600s || true
$OC -n demo-datalake logs job/carga | grep -v -i "warn\|notice" | tail -12
$OC -n demo-datalake get pods; $OC adm top node | tail -1; $OC -n demo-ia-analitica get pods
