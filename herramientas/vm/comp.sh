exec 2>/dev/null </dev/null
export SYSTEMD_PAGER= PAGER=cat LANG=C
echo "release=$(cat /etc/redhat-release)"
[ -f /etc/os-release ] && . /etc/os-release && echo "id=$ID" && echo "version_id=$VERSION_ID"
echo "arch=$(uname -m)"; echo "kernel=$(uname -r)"
echo "cpu_flags=$(grep -m1 '^flags' /proc/cpuinfo | cut -d: -f2)"
if [ -f /etc/sysconfig/rhn/systemid ]; then echo registrado=si
elif command -v subscription-manager >/dev/null; then
  if [ -s /etc/pki/consumer/cert.pem ]; then   # registrado (sin consultar al servidor)
    grep -qs '^hostname *= *subscription.rhsm.redhat.com' /etc/rhsm/rhsm.conf && echo registrado=si || echo registrado=satellite
  else echo registrado=no; fi
else echo registrado=no; fi
R=$( (timeout 60 yum -C -q repolist enabled || yum -C -q repolist enabled) | awk 'NR>1 && $1!="repo" {print $1}' | sed 's#/.*##')
echo "repos=$(echo $R)"
echo "$R" | grep -qi 'els' && echo els=si || echo els=no
echo "repos_terceros=$(echo "$R" | grep -viE '^(rhel-|rhui-|codeready|ansible-|satellite|baseos|appstream|base$|updates$|extras$|centos|rocky|almalinux|ol[0-9]|epel)' | tr '\n' ' ')"
echo "var_libre_mb=$(df -Pm /var | awk 'NR==2 {print $4}')"; echo "boot_libre_mb=$(df -Pm /boot | awk 'NR==2 {print $4}')"
echo "luks=$(lsblk -no TYPE | grep -c crypt)"
echo "nfs=$(grep -v '^#' /etc/fstab | awk '$3 ~ /^nfs/' | wc -l)"
echo "fips=$(cat /proc/sys/crypto/fips_enabled)"
{ rpm -q python2 || rpm -q python | grep -q '^python-2'; } >/dev/null && echo python2=si || echo python2=no
echo "java=$(rpm -qa 'java-*-openjdk-headless' --qf '%{NAME} ' | sed 's/-headless//g')"
echo "ifcfg=$(ls /etc/sysconfig/network-scripts/ifcfg-* | grep -vc ifcfg-lo)"
systemctl is-active -q NetworkManager || { [ -x /etc/init.d/network ] && echo network_scripts=si; }
rpm -q ntp >/dev/null && echo ntpd=si
rpm -q iptables-services >/dev/null && echo iptables=si
grep -qiE '^[[:space:]]*PermitRootLogin' /etc/ssh/sshd_config || echo root_ssh_implicito=si
M=""; for m in $(lsmod | awk 'NR>1 {print $1}'); do s=$(modinfo -F signer $m); case "$s" in *"Red Hat"*|*CentOS*|*Rocky*|*Alma*|*Oracle*) ;; *) [ -n "$(modinfo -F filename $m | grep -v '^(builtin)')" ] && M="$M $m";; esac; done
echo "modulos_terceros=$M"
echo "agentes=$(rpm -qa --qf '%{NAME}\n' | grep -iE 'falcon|sentinel|mcafee|trend|crowdstrike|splunk|zabbix|nagios|datadog|newrelic|dynatrace|commvault|netbackup|veeam|tivoli|qualys|tanium|wazuh|ossec|nessus' | sort -u | tr '\n' ' ')"
A=""; ls -d /opt/jboss* /opt/rh/eap* /opt/*eap* >/dev/null && A="$A JBoss"; rpm -q httpd >/dev/null && A="$A Apache"; rpm -q nginx >/dev/null && A="$A Nginx"
rpm -qa | grep -qE '^(postgresql[0-9]*-server|mariadb-server|mysql-server)' && A="$A base-de-datos"; [ -d /u01 ] || rpm -qa | grep -q oracle && A="$A Oracle"
echo "apps=$A"
if command -v systemctl >/dev/null; then echo "servicios=$(systemctl --no-pager list-units --type=service --state=running --no-legend --plain | awk '{print $1}' | tr '\n' ' ')"
else echo "servicios=$(chkconfig --list | awk '/3:on/ {print $1}' | tr '\n' ' ')"; fi
true
