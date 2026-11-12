#!/bin/bash
# Lectura: estado del SNO y herramientas disponibles en el host. Sin secretos.
echo "SNO: $(sudo virsh domstate sno 2>&1)"
which ansible-playbook ansible-lint python3; ansible --version 2>&1 | head -2
ansible-galaxy collection list 2>/dev/null | grep -E "kubernetes.core|community.general|ansible.utils" 
python3 -c "import kubernetes; print('py-kubernetes', kubernetes.__version__)" 2>&1 | tail -1
D=$HOME/openshift/sno; ls $D/bin; df -h / | tail -1
