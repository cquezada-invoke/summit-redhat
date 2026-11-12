sudo podman exec -i faceops python3 - <<'Q' 2>&1 | tail -60
import json
from app import bd
for e in bd.q("SELECT nombre, servicios, datos FROM equipos WHERE cliente_id=(SELECT id FROM clientes WHERE lower(codigo)='lab') AND nombre IN ('nodo-storage','nodo-nginx','nodo-tomcat','nodo-jboss','nodo-vault','nodo-postgres','nodo-so','dc1-idm01','dc1-ds01','dc1-sat01','dc1-sso01','nodo-windows','dc1-ad01')"):
    s = json.loads(e["servicios"] or "[]")
    print("==", e["nombre"], [x for x in s if not any(k in x.lower() for k in ("systemd","dbus","getty","sshd","chrony","crond","audit","networkmanager","rsyslog","polkit","irqbalance","tuned","user@","gssproxy","sssd","firewalld","kdump","lvm","dracut","atd","rhsm","insights","cloud","dnf","rpc","qemu","serial","selinux","plymouth","logrotate","import-state","nis-domain","microcode","loadmodules","nftables"))])
    d = json.loads(e["datos"] or "{}")
    sd = d.get("servicios_det") or []
    print("   det", [ (x.get("nombre") or x.get("unidad"), (x.get("exec") or x.get("comando") or "")[:110]) for x in sd if any(k in json.dumps(x).lower() for k in ("kafka","artemis","keycloak","lake","weed","redis","vault","postgres","nginx","dirsrv","ipa","foreman","eap","jboss"))][:8])

    print("   keys", [k for k in d.keys()][:60])
Q
