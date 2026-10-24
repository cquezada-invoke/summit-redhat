#!/bin/bash
# Uso: ultimo-job.sh            -> últimos 5 jobs de AAP con estado y etiquetas
#      ultimo-job.sh N [lineas] -> final del stdout del job N
A=$(sed -n 's/^gateway_admin_password=//p' ~/aap/*/inventory-demo)
API=https://localhost/api/controller/v2
if [ -n "$1" ]; then
  curl -sk -u "admin:$A" "$API/jobs/$1/stdout/?format=txt" | tail -n "${2:-40}"
else
  curl -sk -u "admin:$A" "$API/jobs/?order_by=-id&page_size=5" | python3 -c '
import sys,json
for j in json.load(sys.stdin)["results"]:
    et=[l["name"] for l in j["summary_fields"].get("labels",{}).get("results",[])]
    print(j["id"], j["status"], j["name"], "|", ", ".join(et))'
fi
