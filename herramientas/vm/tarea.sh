sudo podman exec faceops sed -n 180,300p /opt/faceops/app/aap.py
sudo podman exec faceops sed -n 340,375p /opt/faceops/app/aap.py
sudo podman exec faceops grep -rn "def disponible\|REPLICA\|replica_aap\|aap_espejo" /opt/faceops/app/aap.py | head
sudo podman exec faceops ls /opt/faceops/app | grep -i "aap\|espejo\|replica"
