cat > ~/pruebas/cola8.sh <<'X'
sleep 150; while pgrep -f "pruebas/cola[1-7]*\.sh" >/dev/null; do sleep 30; done
for p in p-discos2 p-respaldo; do
  cd ~/summit-redhat && git stash -q; git pull -q origin main; git stash pop -q
  echo "=== $p $(date +%H:%M)"; bash herramientas/pruebas/$p.sh
done
echo FIN-COLA8
X
env -u RUNNER_TRACKING_ID setsid nohup bash ~/pruebas/cola8.sh > ~/pruebas/cola8.log 2>&1 < /dev/null & echo cola8 en espera
