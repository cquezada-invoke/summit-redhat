#!/bin/bash
# Switches del laboratorio (Nokia SR Linux, spine-leaf): despliega summit-sw si no está arriba. Idempotente.
D=$(cd "$(dirname "$0")/../lab/switches" && pwd)
if sudo podman ps --format '{{.Names}}' | grep -qx sw-core; then echo "switches: arriba"; exit 0; fi
sudo podman pull -q ghcr.io/nokia/srlinux:24.10.1 >/dev/null
sudo podman pull -q docker.io/library/alpine:3.20 >/dev/null
cd "$D" && sudo containerlab deploy -t summit-sw.clab.yml --reconfigure 2>&1 | tail -12
