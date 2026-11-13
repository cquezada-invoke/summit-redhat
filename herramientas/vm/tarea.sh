#!/bin/bash
# AAP no ejecuta trabajos (pendientes): reinicio de controller-task y controller-web. Sin secretos.
podman ps --format '{{.Names}} {{.Status}}' | grep -E "controller|gateway|receptor"
systemctl --user list-units --no-legend 'automation-controller*' 2>/dev/null | head
podman exec automation-controller-task awx-manage list_instances 2>&1 | head -10
for u in automation-controller-task automation-controller-web; do
  if systemctl --user cat $u >/dev/null 2>&1; then systemctl --user restart $u; else podman restart $u >/dev/null; fi; echo "reiniciado $u"
done
sleep 60
podman exec automation-controller-task awx-manage list_instances 2>&1 | head -10
for i in $(seq 1 20); do c=$(curl -sk -o /dev/null -w %{http_code} https://localhost/api/controller/v2/ping/); [ "$c" = 200 ] && break; sleep 10; done; echo "ping $c"
