for i in $(seq 1 100); do [ $(grep -c FIN-LOOP /tmp/arnes-vo.log) -ge 2 ] && break; sed -n '/vuelta 1 09:/,$p' /tmp/arnes-vo.log | grep -q "== C1" && sed -n '/vuelta 1 09:/,$p' /tmp/arnes-vo.log | grep -A6 "== C1" | grep -q "=>" && break; sleep 30; done
sed -n '/vuelta 1 09:/,$p' /tmp/arnes-vo.log | grep -v -i -E "warn|token|password|secret" | tail -40
