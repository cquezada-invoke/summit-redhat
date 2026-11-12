export KUBECONFIG=$HOME/openshift/sno/instalar/auth/kubeconfig; OC=$HOME/openshift/sno/bin/oc
$OC describe node sno | grep -A9 "Allocated resources"
$OC get ns openshift-file-integrity openshift-netobserv-operator 2>&1; $OC get operators.operators.coreos.com
$OC get csv -n openshift-file-integrity --no-headers 2>&1 | head; $OC get all -n openshift-netobserv-operator 2>&1 | head
for p in netobserv-operator cluster-observability-operator openshift-cert-manager-operator; do $OC get packagemanifest $p -n openshift-marketplace -o json | python3 -c "
import json,sys;d=json.load(sys.stdin)['status'];c=[x for x in d['channels'] if x['name']==d['defaultChannel']][0]
print('$p', [(dp['name'],[ct.get('resources',{}).get('requests') for ct in dp['spec']['template']['spec']['containers']]) for dp in c['currentCSVDesc'].get('installStrategy',{}).get('spec',{}).get('deployments',[])] if 'installStrategy' in c['currentCSVDesc'] else list(c['currentCSVDesc'].keys()))" 2>&1 | cut -c1-600; done
