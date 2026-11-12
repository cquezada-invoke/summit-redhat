# Depuración: podman del usuario de AAP visto por la herramienta (solo lectura)
U=$(id -u cquezada); echo "uid $U"
cd /tmp && sudo runuser -u cquezada -- env XDG_RUNTIME_DIR=/run/user/$U DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$U/bus HOME=/home/cquezada podman ps -a --format json 2>&1 | head -c 400; echo
sudo /usr/local/sbin/faceops-autocuidado reiniciar-aap --modo evaluar --sin-propios 2>&1 | head -c 600; echo
sudo /usr/local/sbin/faceops-autocuidado estado 2>&1 | python3 -c "import json,sys;d=json.load(sys.stdin);print('api',d['aap_api'],'faceops',d['faceops'],'vms',None if d['vms'] is None else len(d['vms']),'cont',None if d['contenedores'] is None else len(d['contenedores']))"
