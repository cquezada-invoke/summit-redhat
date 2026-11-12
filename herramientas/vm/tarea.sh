#!/bin/bash
# Borra ramas vmt/* cuyo SHA ya tiene resultado en vm-resultados, con la credencial del job (contents: write).
# Nunca toca ramas sin resultado. No imprime secretos.
cd "${GITHUB_WORKSPACE:-.}" || exit 1
git fetch -q --depth 1 origin vm-resultados || { echo "ERR fetch resultados"; exit 1; }
git grep -h "^commit: " FETCH_HEAD -- 'resultados/[0-9]*.txt' | awk '{print $2}' | sort -u > /tmp/hechos.txt
git ls-remote origin 'refs/heads/vmt/*' > /tmp/ramas.txt
awk 'NR==FNR{d[$1]=1;next} ($1 in d){print $2}' /tmp/hechos.txt /tmp/ramas.txt > /tmp/borrar.txt
echo "ramas=$(wc -l < /tmp/ramas.txt) con_resultado=$(wc -l < /tmp/borrar.txt) sin_resultado=$(( $(wc -l < /tmp/ramas.txt) - $(wc -l < /tmp/borrar.txt) ))"
rm -f /tmp/lote.*; split -l 50 /tmp/borrar.txt /tmp/lote.
ok=0; err=0
for f in /tmp/lote.*; do
  r=1; for i in 1 2 3; do timeout 120 git push -q origin $(sed 's/^/:/' $f) >/tmp/push.log 2>&1 && { r=0; break; }; sleep 3; done
  [ $r = 0 ] && ok=$((ok+$(wc -l < $f))) || { err=$((err+1)); grep -v -i -E "token|http|auth" /tmp/push.log | head -2; }
done
echo "borradas=$ok lotes_con_error=$err"
echo "quedan=$(git ls-remote origin 'refs/heads/vmt/*' | wc -l)"
rm -f /tmp/lote.* /tmp/push.log
