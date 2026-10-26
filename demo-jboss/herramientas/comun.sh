# Funciones comunes de las herramientas de jboss-config (se incluye con "source").
SEC=~/demo-drift/cicd-secretos.txt
GT=$(sed -n 's/^gitea_token=//p' $SEC)
lanzar(){ # lanzar "<plantilla>" '<extra_vars json>'  → espera y muestra el resultado
  local E=${2:-'{}'}
  PYTHONPATH=~/bin python3 - "$1" "$E" <<'PY'
import sys, json, time, urllib.parse
from aapapi import aap
jt = aap("GET", "job_templates/?name=" + urllib.parse.quote(sys.argv[1]))["results"][0]
while aap("GET", f"job_templates/{jt['id']}/jobs/?status__in=pending,waiting,running")["count"]:
    time.sleep(3)   # una sola ejecución sobre JBoss a la vez
j = aap("POST", f"job_templates/{jt['id']}/launch/", {"extra_vars": json.loads(sys.argv[2])})
print(f"   AAP: '{sys.argv[1]}' job {j['id']} · https://34.176.42.56/execution/jobs/playbook/{j['id']}/output", flush=True)
while (st := aap("GET", f"jobs/{j['id']}/")["status"]) not in ("successful", "failed", "error", "canceled"):
    time.sleep(4)
print(f"   resultado: {st}")
PY
}
estado(){ # muestra lo relevante de cada JBoss
  for h in nodo-jboss nodo-jboss2; do
    printf "   %-12s app %-12s · log raíz %-6s · pool máx %s\n" "$h" \
      "$(curl -s -m5 http://$h:8080/app-jboss/salud | tr -d '\n')" \
      "$(ssh $h "sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh --connect --user=$(sed -n 's/^jboss_usuario=//p' $SEC) --password=$(sed -n 's/^jboss_clave=//p' $SEC) --command='/subsystem=logging/root-logger=ROOT:read-attribute(name=level)'" 2>/dev/null | sed -n 's/.*"result" => "\(.*\)".*/\1/p')" \
      "$(ssh $h "sudo /opt/rh/eap8/root/usr/share/wildfly/bin/jboss-cli.sh --connect --user=$(sed -n 's/^jboss_usuario=//p' $SEC) --password=$(sed -n 's/^jboss_clave=//p' $SEC) --command='/subsystem=datasources/data-source=app-jboss:read-attribute(name=max-pool-size)'" 2>/dev/null | sed -n 's/.*"result" => \([0-9]*\).*/\1/p')"
  done
}
