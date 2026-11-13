#!/bin/bash
SHA=3c2d46a3; for i in $(seq 1 90); do D=$(cat ~/faceops-autodeploy/desplegado 2>/dev/null); git -C ~/summit-redhat merge-base --is-ancestor $SHA $D 2>/dev/null && break; sleep 10; done
cut -c1-200 ~/faceops-autodeploy/estado
echo ---vin; grep -a "CASO\|ESTADO\|AUTO\|WIN\|Traceback\|Error" /tmp/a21-b05.log | cut -c1-250 | tail -15
sudo podman ps --format "{{.Names}} {{.Status}}" | grep -E "^faceops |fo-a21"
ps aux | grep -c "[t]area.sh"
