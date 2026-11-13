#!/bin/bash
for i in $(seq 1 30); do sudo podman ps --format "{{.Names}} {{.Status}}" | grep -q "^faceops " && break; sleep 10; done
sudo podman ps --format "{{.Names}} {{.Status}}" | grep -E "^faceops|fo-a21"; cut -c1-200 ~/faceops-autodeploy/estado; sudo systemctl status faceops --no-pager 2>&1 | head -5
