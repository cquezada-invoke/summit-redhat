S="ssh -i $HOME/.ssh/id_demo -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 cquezada@34.176.114.77"
$S 'cd ~/demo-gcp; sed -n "/DIAG == DIAG log/,/DIAG == DIAG pods/p" imagen.log | awk "!s[\$0]++" | head -120'
