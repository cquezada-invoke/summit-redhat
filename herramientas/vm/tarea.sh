#!/bin/bash
sudo podman ps --format '{{.Names}} {{.Status}}' | grep -E "^fo-" ; ps aux | grep -E "tarea.sh|herramientas/vm" | grep -v grep | cut -c1-160 | head
