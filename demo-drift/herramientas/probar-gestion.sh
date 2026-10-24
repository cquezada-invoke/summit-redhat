#!/usr/bin/env bash
# Prueba la gestión de servidores contra un panel (por defecto el de prueba en 8089).
# Uso: probar-gestion.sh agregar|quitar|estado|visitante|evento SERVIDOR [nivel] [visitantes] [sudo]
P=${PANEL:-http://127.0.0.1:8089}
estado(){ curl -s $P/api/gestion | python3 -c 'import sys,json;g=json.load(sys.stdin)["gestion"];[print(h,v.get("estado"),v.get("nivel"),"vis="+str(v.get("visitantes")),"sudo="+str(v.get("sudo")),"job="+str(v.get("job")),v.get("mensaje","")) for h,v in g.items()]'; }
esperar(){ for _ in $(seq 1 60); do sleep 3; curl -s $P/api/gestion | grep -qE 'incorporando|retirando' || break; done; estado; }
case "$1" in
  agregar) curl -s -XPOST $P/api/gestion/agregar -H 'Content-Type: application/json' \
             -d "{\"servidor\":\"$2\",\"nivel\":\"${3:-corregir}\",\"visitantes\":${4:-true},\"sudo\":${5:-true},\"grupo_admin\":\"dba\",\"frecuencia\":0,\"correo\":\"cquezada@invoke.cl\"}"; echo; esperar ;;
  quitar) curl -s -XPOST $P/api/gestion/quitar -H 'Content-Type: application/json' -d "{\"servidor\":\"$2\"}"; echo; esperar ;;
  estado) estado ;;
  visitante) curl -s -XPOST $P/visitante --data-urlencode accion=crear --data-urlencode nombre=Prueba --data-urlencode correo=prueba.gestion@invoke.cl \
               | grep -oE 'Usuario: <b>[^<]+|ssh [a-z0-9]+@[a-z-]+|· (con|sin) sudo|Ningún servidor[^<]*' ;;
  evento) curl -s -XPOST $P/api/eventos -H 'Content-Type: application/json' -d "{\"id\":\"p$RANDOM\",\"host\":\"$2\",\"usuario\":\"jperez\",\"como\":\"root\",\"comandos\":[\"prueba\"],\"archivos\":[\"/etc/ssh/sshd_config\"]}"
          sleep 25; curl -s $P/api/incidentes | python3 -c 'import sys,json;[print(i["host"],i["estado"],i["correcciones"]) for i in json.load(sys.stdin)[:3]]' ;;
esac
