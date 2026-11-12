#!/bin/bash
# Lectura: contenido del SNO relevante para imágenes y prueba de entorno virtual para Ansible. Sin secretos.
D=$HOME/openshift/sno; export KUBECONFIG=$D/instalar/auth/kubeconfig; OC=$D/bin/oc
$OC get nodes --no-headers
echo "IS: $($OC get is -A --no-headers 2>/dev/null | wc -l)  ISTAG: $($OC get istag -A --no-headers 2>/dev/null | wc -l)  IMAGES: $($OC get images --no-headers 2>/dev/null | wc -l)"
$OC get is -A --no-headers | awk '{print $1}' | sort | uniq -c | sort -rn | head -15
$OC get configs.imageregistry.operator.openshift.io cluster -o jsonpath='{.spec.managementState} {.spec.storage}{"\n"}' | sed -E 's/"(accessKey|secretKey)[^,}]*//g'
$OC get node -o jsonpath='{range .items[*]}{.metadata.name} imgs={.status.images[*].sizeBytes}{"\n"}{end}' | awk '{print $1, NF}'
$OC get csv -A --no-headers 2>/dev/null | awk '{print $2}' | sort -u | head
python3 --version; ls ~/venv-imagenes 2>/dev/null | head -2
python3 -m venv /tmp/venvprueba && /tmp/venvprueba/bin/pip install -q kubernetes 2>&1 | tail -2; /tmp/venvprueba/bin/python -c "import kubernetes;print('ok', kubernetes.__version__)"; rm -rf /tmp/venvprueba
ansible-galaxy collection list 2>/dev/null | grep -iE "kubernetes|general"
