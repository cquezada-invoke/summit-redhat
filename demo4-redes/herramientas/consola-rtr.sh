#!/bin/bash
# Consola del router (vtysh), como entraría un operador a mano.
exec sudo podman exec -it clab-summit-red-rtr vtysh
