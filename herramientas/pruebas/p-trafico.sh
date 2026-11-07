# Plan «Despliegue · Cambiar tráfico» sobre nodo-nginx (azul = nodo-jboss, verde = nodo-jboss-dev)
source ~/summit-redhat/herramientas/pruebas/comun.sh
ok_() { [ "$(estado)" = exitoso ]; }
for m in nodo-nginx nodo-jboss nodo-jboss-dev; do energia $m encender; done
IN=$(ip_de nodo-nginx); IA=$(ip_de nodo-jboss); IV=$(ip_de nodo-jboss-dev); echo "nginx $IN · azul $IA · verde $IV"
N="Despliegue · Cambiar tráfico"
dt() { caso "{\"auto\":\"$N\",\"equipo\":\"nodo-nginx\",\"params\":{\"dt_app\":\"app-jboss\",\"dt_estrategia\":\"$1\",\"dt_azul\":\"$IA\",\"dt_verde\":\"$IV\",\"dt_destino\":\"$2\",\"dt_peso_verde\":${3:-10},\"dt_ruta\":\"/app-jboss/\"},\"min\":15,\"caso\":\"$4\"}"; }
med() { en $IN "for i in \$(seq 1 ${1:-200}); do curl -s -o /dev/null -D - $2 http://127.0.0.1:8009/app-jboss/ | awk 'tolower(\$1)==\"x-servidor:\"{print \$2}' | tr -d '\r' | cut -d: -f1; done | sort | uniq -c | tr '\n' ' '"; }
pct() { python3 -c "import re,sys;d={b:int(a) for a,b in re.findall(r'(\d+) (\S+)', sys.argv[1])};t=sum(d.values()) or 1;print(round(100*d.get(sys.argv[2],0)/t))" "$1" "$2"; }
dt blue-green verde 10 C1; M1=$(med 20); echo "C1 $M1"
ok_ && [ "$(pct "$M1" $IV)" = 100 ] && anotar C1 "blue-green todo a verde" ok "$M1" || anotar C1 "blue-green todo a verde" falla "$(estado) $M1 $(jq_ "r.get('detalle')")"
dt canary verde 50 C2; M2=$(med 200); P2=$(pct "$M2" $IV); echo "C2 $M2 → $P2 %"
ok_ && [ "$P2" -ge 35 ] && [ "$P2" -le 65 ] && anotar C2 "canary 50 %" ok "$P2 % al verde de 200 ($M2)" || anotar C2 "canary 50 %" falla "$(estado) $P2 % $M2"
dt ab verde 10 C3; M3a=$(med 20 "-b faceops_grupo=b"); M3b=$(med 20); echo "C3 cookie: $M3a · sin cookie: $M3b"
ok_ && [ "$(pct "$M3a" $IV)" = 100 ] && [ "$(pct "$M3b" $IA)" = 100 ] && anotar C3 "ab por cookie" ok "con cookie $M3a · sin cookie $M3b" || anotar C3 "ab por cookie" falla "$(estado) $M3a / $M3b"
dt shadow azul 10 C4; M4=$(med 20); CF=$(en $IN 'grep -c "mirror /fo_espejo" /etc/nginx/conf.d/faceops-trafico-app-jboss.conf')
ok_ && [ "$(pct "$M4" $IA)" = 100 ] && [ "$CF" = 1 ] && anotar C4 "shadow" ok "respuestas 100 % azul ($M4); espejo al verde configurado" || anotar C4 "shadow" falla "$(estado) $M4 espejo=$CF"
dt blue-green azul 10 C5; M5=$(med 20); NT=$(en $IN 'sudo nginx -t 2>&1 | tail -1')
ok_ && [ "$(pct "$M5" $IA)" = 100 ] && echo "$NT" | grep -q successful && anotar C5 "todo a azul" ok "$M5 · $NT" || anotar C5 "todo a azul" falla "$(estado) $M5 $NT"
certificar "$N" "Plan cambiar tráfico · C1–C5"
for m in nodo-nginx nodo-jboss nodo-jboss-dev; do energia $m apagar; done
echo FIN-TRAFICO
