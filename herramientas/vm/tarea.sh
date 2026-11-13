#!/bin/bash
cat ~/faceops-autodeploy/estado | cut -c1-600
cd ~/summit-redhat && git status -sb | head -15; git log -3 --format='%h %ad %s' --date=iso | cut -c1-140; git fetch -q origin main; git log -3 --format='%h %s' FETCH_HEAD | cut -c1-120; git merge-base --is-ancestor HEAD FETCH_HEAD && echo "HEAD ancestro de origin/main" || echo "DIVERGE"; git log --oneline FETCH_HEAD..HEAD | head -5
tail -20 ~/faceops-autodeploy/*.log 2>/dev/null | cut -c1-300 | tail -20
