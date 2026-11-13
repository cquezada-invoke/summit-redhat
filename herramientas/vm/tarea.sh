#!/bin/bash
cd ~/summit-redhat
git log --format='%h %an <%ae> %ad' --date=iso origin/main..HEAD
git diff --stat origin/main...HEAD | tail -8
git -c user.name='Cristian Quezada' -c user.email=cquezada@invoke.cl pull -q --rebase --autostash origin main 2>&1 | tail -5
git status -sb | head -3
if git merge-base --is-ancestor origin/main HEAD; then
  git push -q origin HEAD:main 2>&1 | grep -v "^remote" | tail -3; echo "push rc=$?"
  git fetch -q origin main; git status -sb | head -1
else echo "NO REBASE"; git rebase --abort 2>/dev/null; fi
