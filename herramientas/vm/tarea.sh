#!/bin/bash
# Diagnóstico (solo lectura) de por qué nodo-so no llega a los repos de Red Hat. Sin secretos.
VM=nodo-so
echo "== host: $(date -u +%FT%TZ)"; ls ~/.ssh 2>/dev/null | grep -v -E "\.pub$|known_hosts|config|authorized" | tr '\n' ' '; echo
IP=$(sudo virsh domifaddr $VM 2>/dev/null | awk '/ipv4/ {sub("/.*","",$4); print $4; exit}')
[ -z "$IP" ] && IP=$(sudo virsh domifaddr $VM --source arp 2>/dev/null | awk '/ipv4/ {sub("/.*","",$4); print $4; exit}')
echo "== IP $VM: ${IP:-?}"
ACC=""
for k in ~/.ssh/id_demo ~/.ssh/id_ed25519 ~/.ssh/id_rsa ~/.ssh/faceops* ~/faceops/*.key ~/faceops/id_*; do
  [ -f "$k" ] || continue; case "$k" in *.pub) continue;; esac
  for u in root cloud-user cquezada ansible faceops; do
    if timeout 12 ssh -i "$k" -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR $u@$IP true 2>/dev/null; then
      ACC="ssh -i $k -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR $u@$IP"; echo "acceso: ssh $u con $(basename $k)"; break 2; fi
  done
done
[ -z "$ACC" ] && { echo "sin acceso ssh; agente qemu:"; sudo virsh qemu-agent-command $VM '{"execute":"guest-ping"}' 2>&1 | head -2; }
cat > /tmp/diag-so.sh <<'EOF'
S=""; [ "$(id -u)" = 0 ] || S="sudo -n"
echo "-- fecha VM: $(date -u +%FT%TZ) · chrony: $(chronyc tracking 2>/dev/null | awk -F: '/System time|Leap/ {printf "%s;", $2}')"
echo "-- release: $(cat /etc/redhat-release)"
echo "-- rhsm status:"; $S subscription-manager status 2>&1 | grep -v -i -E "org|key" | head -8
echo "-- identity registrada: $($S subscription-manager identity >/dev/null 2>&1 && echo si || echo no)"
echo "-- sca/owner:"; $S subscription-manager list --consumed 2>&1 | grep -E "Subscription Name|Status Details|Ends" | head -4
echo "-- rhsm.conf (sin claves):"; grep -E "^\s*(hostname|baseurl|proxy_hostname|proxy_port|no_proxy|repo_ca_cert|manage_repos)\s*=" /etc/rhsm/rhsm.conf 2>/dev/null
echo "-- dnf proxy: $(grep -h -E '^\s*proxy\s*=' /etc/dnf/dnf.conf /etc/yum.repos.d/*.repo 2>/dev/null | sed 's#//[^@]*@#//***@#' | head -2)"
echo "-- env proxy: $(env | grep -i -E '^(https?|no)_proxy' | sed 's#//[^@]*@#//***@#' | tr '\n' ' ')"
echo "-- resolv: $(grep -E '^nameserver' /etc/resolv.conf | tr '\n' ' ')"
echo "-- DNS cdn.redhat.com: $(getent hosts cdn.redhat.com | head -1)"
echo "-- DNS subscription.rhsm.redhat.com: $(getent hosts subscription.rhsm.redhat.com | head -1)"
echo "-- ruta: $(ip route | head -3 | tr '\n' ';')"
echo "-- https cdn: $(curl -s -o /dev/null -w '%{http_code} %{time_total}s' --max-time 15 https://cdn.redhat.com/ 2>&1)"
echo "-- https rhsm: $(curl -s -o /dev/null -w '%{http_code} %{time_total}s' --max-time 15 https://subscription.rhsm.redhat.com/subscription/ 2>&1)"
echo "-- repos habilitados:"; $S dnf -q repolist --enabled 2>&1 | head -6
echo "-- certificados de entitlement: $(ls /etc/pki/entitlement 2>/dev/null | wc -l) · vence: $(for c in /etc/pki/entitlement/*[0-9].pem; do [ -f "$c" ] && openssl x509 -noout -enddate -in "$c"; done 2>/dev/null | head -1)"
echo "-- prueba metadata baseos:"; $S dnf -q makecache --repo rhel-9-for-x86_64-baseos-rpms 2>&1 | tail -3
EOF
if [ -n "$ACC" ]; then $ACC 'bash -s' < /tmp/diag-so.sh 2>&1 | grep -v -i -E "activationkey|password" | head -60; fi
