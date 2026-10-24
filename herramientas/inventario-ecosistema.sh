#!/bin/bash
# Inventario de solo lectura del ecosistema del stand (no muestra claves).
# Uso: bash ~/summit-redhat/herramientas/inventario-ecosistema.sh > ~/inventario.txt
export PATH=$PATH:/usr/local/bin KUBECONFIG=/etc/rancher/k3s/k3s.yaml
S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o LogLevel=ERROR"
M=http://metadata.google.internal/computeMetadata/v1/instance
g(){ curl -s -m 3 -H Metadata-Flavor:Google "$M/$1"; }
echo "== HOST"
echo "tipo: $(g machine-type|awk -F/ '{print $NF}') | zona: $(g zone|awk -F/ '{print $NF}') | ip: $(g network-interfaces/0/access-configs/0/external-ip)"
echo "cpu: $(nproc) vCPU | $(lscpu|awk -F: '/^Core\(s\) per socket/{c=$2}/^Socket\(s\)/{s=$2}END{print c*s}') cores fisicos | $(lscpu|sed -n 's/^Model name: *//p')"
free -m|awk '/Mem/{printf "ram: total %.1fG | usada %.1fG | disponible %.1fG\n",$2/1024,$3/1024,$7/1024}'
df -h /|awk 'NR==2{print "disco /: "$2" | usado "$3" ("$5")"}'
echo "so: $(. /etc/os-release; echo $PRETTY_NAME) | kernel $(uname -r) | carga: $(cut -d' ' -f1-3 /proc/loadavg)"
echo "kvm qemu RSS total: $(ps -eo rss,comm|awk '/qemu/{s+=$1}END{print int(s/1024)"M"}')"
echo "== NODOS"
declare -A IP=([nginx]=11 [so]=12 [postgres]=13 [tomcat]=14 [jboss]=15)
for n in nginx so postgres tomcat jboss; do
 d=nodo-$n; ip=192.168.122.${IP[$n]}
 vi=$(sudo virsh dominfo $d 2>/dev/null|awk -F: '/^CPU\(s\)/{c=$2}/^Max memory/{m=$2}/^State/{s=$2}END{gsub(/ /,"",c);gsub(/[^0-9]/,"",m);gsub(/^ +/,"",s);printf "%s vCPU | %.1fG RAM asignada | %s",c,m/1048576,s}')
 echo "-- $d ($ip): $vi"
 $S ansible@$ip 'echo "  so: $(. /etc/os-release; echo $PRETTY_NAME) | kernel $(uname -r)"
  free -m|awk "/Mem/{printf \"  ram: usada %dM de %dM\n\",\$3,\$2}"
  df -h /|awk "NR==2{print \"  disco /: \"\$2\" | usado \"\$3\" (\"\$5\")\"}"
  echo "  paquetes: $(rpm -qa nginx postgresql-server eap8-wildfly podman 2>/dev/null|sort|tr "\n" " ")"
  echo "  servicios activos: $(for s in nginx postgresql eap8-standalone sshd; do systemctl is-active -q $s 2>/dev/null && printf "%s " $s; done)"
  c=$(sudo podman ps --format "{{.Names}}={{.Image}}" 2>/dev/null|tr "\n" " "); [ -n "$c" ] && echo "  contenedores: $c"
  m=$(sudo podman stats --no-stream --format "{{.Name}}={{.MemUsage}}" 2>/dev/null|tr "\n" " "); [ -n "$m" ] && echo "  ram contenedores: $m"
  command -v psql >/dev/null && echo "  postgres: $(psql --version|cut -d" " -f3)"
  [ -d /opt/rh/eap8 ] && echo "  jboss: $(ls /opt/rh/eap8/root/usr/share/wildfly/modules/system/layers/base/org/jboss/as/server/main/*.jar 2>/dev/null|xargs -n1 basename|head -1)"' 2>&1 | head -12
done
echo "== AAP"
echo "bundle: $(ls -d ~/aap/*/ 2>/dev/null|xargs -n1 basename|tr '\n' ' ')"
echo "controller: $(curl -sk -m 5 https://localhost/api/controller/v2/ping/|python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("version"),"| nodos:",len(d.get("instances",[])))' 2>/dev/null)"
echo "eda: $(podman ps --format '{{.Names}}'|grep -ci eda) contenedores"
echo "== CONTENEDORES HOST (rootless cquezada)"
podman ps --format '{{.Names}}|{{.Image}}' | awk -F'|' '{n=split($2,a,"/");print "  "$1" = "a[n]}'
echo "-- ram:"
podman stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null|awk '{print "  "$1": "$2}'
echo "== K3S / TEKTON"
echo "k3s: $(k3s --version 2>/dev/null|head -1|awk '{print $3}') | ram k3s: $(ps -eo rss,comm|awk '$2~/k3s/{s+=$1}END{print int(s/1024)"M"}')"
for c in pipelines-info:pipelines triggers-info:triggers dashboard-info:dashboard; do v=$(kubectl -n tekton-pipelines get cm ${c%%:*} -o jsonpath='{.data.version}' 2>/dev/null); echo "tekton ${c##*:}: ${v:-no encontrado}"; done
echo "pipelines: $(kubectl get pipelines -A --no-headers 2>/dev/null|awk '{print $2}'|tr '\n' ' ') | eventlisteners: $(kubectl get eventlisteners -A --no-headers 2>/dev/null|awk '{print $2}'|tr '\n' ' ')"
echo "servicios nodeport: $(kubectl get svc -A --no-headers 2>/dev/null|awk '$3=="NodePort"{print $2"="$6}'|tr '\n' ' ')"
echo "pipelineruns: $(kubectl get pipelineruns -A --no-headers 2>/dev/null|wc -l)"
echo "== OTROS"
echo "gitea: $(curl -s -m 3 localhost:3000/api/v1/version)"
echo "vault: $(curl -sk -m 3 https://127.0.0.1:8200/v1/sys/health|python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["version"],"sealed="+str(d["sealed"]))' 2>/dev/null)"
echo "puertos:"; sudo ss -ltnpH 2>/dev/null|awk '{split($4,a,":");p=a[length(a)];if(p~/^(443|3000|8090|8200|8202|9418|9443|30080|30097|8025|22)$/){match($6,/"[^"]+"/);print "  "p" "substr($6,RSTART,RLENGTH)}}'|sort -n -k1 -u
echo "units usuario: $(systemctl --user list-units --type=service --state=running --no-legend --plain 2>/dev/null|awk '{print $1}'|grep -vE '^(dbus|podman|systemd)'|tr '\n' ' ')"
echo "units sistema: $(systemctl list-units --type=service,socket --state=running,listening --no-legend --plain 2>/dev/null|awk '{print $1}'|grep -iE 'git|panel|drift|nginx|haproxy|k3s|libvirt|proxy|caddy'|tr '\n' ' ')"
echo "== TOTAL"
free -m|awk '/Mem/{printf "ram usada %.1fG de %.1fG\n",$3/1024,$2/1024}'
top -bn1|awk '/Cpu\(s\)/{print "cpu: "$0}'|head -1
