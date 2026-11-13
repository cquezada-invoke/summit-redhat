#!/bin/bash
cd ~/summit-redhat && git -c user.name='FaceOps autodeploy' -c user.email=cquezada@invoke.cl pull -q --rebase --autostash origin main 2>&1 | tail -2; git status -sb | head -1; git log -1 --format=%h
bash herramientas/faceops-autodeploy.sh 2>&1 | tail -3; cut -c1-200 ~/faceops-autodeploy/estado
