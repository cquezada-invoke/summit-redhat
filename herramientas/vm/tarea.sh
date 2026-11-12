tail -15 ~/faceops-autodeploy/build.log | grep -v -i "token\|clave\|pass"; cat ~/faceops-autodeploy/estado; cd ~/summit-redhat && git log -3 --format="%h %s" | cut -c1-90
