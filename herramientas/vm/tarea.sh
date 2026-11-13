#!/bin/bash
sudo podman rm -f fo-b05 >/dev/null 2>&1; echo "fo-b05 detenido"; sudo podman ps --format '{{.Names}}' | grep '^fo-' || true
