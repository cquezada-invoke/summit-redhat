#!/bin/bash
# Demo 4 · caso "actualización masiva de firmware": instala lo necesario en la VM (idempotente).
#   1) Routers de sucursal suc1-3 (topología summit-suc, FRR 9.1.0) en la red de gestión del laboratorio.
#   2) Imagen "defectuosa" para la demo de vuelta atrás (FRR 10.2.1 sin BGP).
#   3) /usr/local/sbin/lab-sucursal + usuario aap-lab (sudo SOLO para ese script) para que AAP opere los routers.
#   4) Llave del laboratorio autorizada en nodo-so y nodo-nginx (usuario ansible) para los parches de SO.
set -e
D=$(cd "$(dirname "$0")/.." && pwd); L=/opt/summit-red
# La topología se copia solo la primera vez: después lab-sucursal mantiene en ella la imagen vigente de cada router
[ -f $L/summit-suc.clab.yml ] || sudo cp "$D/lab/summit-suc.clab.yml" $L/
for n in 1 2 3; do sudo cp "$D/lab/config/suc$n.conf" $L/config/; done
sudo podman image exists quay.io/frrouting/frr:9.1.0 || sudo podman pull -q quay.io/frrouting/frr:9.1.0
sudo podman image exists quay.io/frrouting/frr:10.2.1 || sudo podman pull -q quay.io/frrouting/frr:10.2.1
sudo podman image exists localhost/frr:10.2.1-defectuosa || sudo podman build -q -t localhost/frr:10.2.1-defectuosa "$D/lab/frr-defectuosa" >/dev/null
sudo install -m 0755 -o root -g root "$D/lab/lab-sucursal" /usr/local/sbin/lab-sucursal
id aap-lab >/dev/null 2>&1 || sudo useradd -m -c "AAP - operación del laboratorio de red" aap-lab
sudo install -d -m 0700 -o aap-lab -g aap-lab /home/aap-lab/.ssh
sudo install -m 0600 -o aap-lab -g aap-lab ~/demo4/llave-lab.pub /home/aap-lab/.ssh/authorized_keys
sudo restorecon -R /home/aap-lab/.ssh 2>/dev/null || true
echo 'aap-lab ALL=(root) NOPASSWD: /usr/local/sbin/lab-sucursal' | sudo tee /etc/sudoers.d/aap-lab >/dev/null
sudo chmod 0440 /etc/sudoers.d/aap-lab && sudo visudo -cq
# Routers de sucursal: se despliegan solo si no existen (no se tocan los demás contenedores del laboratorio)
if ! sudo podman container exists clab-summit-suc-suc1; then
  (cd $L && sudo containerlab deploy -t $L/summit-suc.clab.yml >/tmp/summit-suc-deploy.log 2>&1) || { tail -20 /tmp/summit-suc-deploy.log; exit 1; }
fi
P=$(cat ~/demo4/llave-lab.pub)
for h in nodo-so nodo-nginx; do
  ssh $h "grep -qF '$P' ~/.ssh/authorized_keys || echo '$P' >> ~/.ssh/authorized_keys"
done
for n in 1 2 3; do sudo /usr/local/sbin/lab-sucursal estado suc$n; done
