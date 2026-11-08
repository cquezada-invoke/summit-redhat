# Plan «Sistema · Inventariar máquina» · primera tanda (C1 en dos máquinas y C2). No certifica: faltan C3–C11.
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Sistema · Inventariar máquina"
TK=$(sudo podman exec faceops printenv FACEOPS_RESPALDO_TOKEN | tr -d "\r")
G=http://192.168.122.1:3000
git_() { curl -s -H "Authorization: token $TK" "$G/api/v1$1"; }
revisar() {  # revisar <maquina>
  local d=repositorio
  git_ "/repos/$d/maquina-$1/commits?sha=master&limit=3&stat=false&files=false" | python3 -c 'import json,sys
cs=json.load(sys.stdin)
for c in cs if isinstance(cs,list) else []: print("  commit", c["sha"][:7], c["commit"]["message"].splitlines()[0][:120])'
  git_ "/repos/$d/maquina-$1/git/trees/master?recursive=true&per_page=10000" | python3 -c 'import json,sys
t=[x["path"] for x in (json.load(sys.stdin).get("tree") or []) if x["type"]=="blob"]
a=[x for x in t if x.startswith("archivos/")]; m=[x for x in t if x.startswith("meta/")]
mal=[x for x in t if any(k in x for k in ("shadow","ssh_host_","/private/",".key",".jks","git-credentials"))]
print("  archivos", len(a), "· meta", sorted(x[5:] for x in m), "· secretos en git", mal[:5])
print("  etc/hosts", "archivos/etc/hosts" in t, "· etc/passwd", "archivos/etc/passwd" in t)'
  git_ "/repos/$d/maquina-$1/raw/meta/directorios.txt?ref=master" | grep -c "" | sed 's/^/  carpetas registradas /'
  git_ "/repos/$d/maquina-$1/raw/meta/directorios.txt?ref=master" | grep -E "directorio-creado|otro-directorio" | sed 's/^/  /'
  git_ "/repos/$d/maquina-$1/raw/meta/cobertura.txt?ref=master" | cut -c1-140 | head -12 | sed 's/^/  cob /'
}
for M in srv-prueba-rhel9-01 nodo-vault; do
  caso "{\"auto\":\"$N\",\"equipo\":\"$M\",\"params\":{},\"min\":25,\"caso\":\"C1\"}" > /dev/null
  echo "== C1 $M: $(estado) $(ticket)"
  jq_ "{k: {x: y for x, y in v.items() if x != 'archivo'} for k, v in (s.get('faceops_inventario_git') or {}).items()}"
  jq_ "[x for v in (s.get('faceops_inventario_git') or {}).values() for x in v.get('archivo', []) if not x.startswith('COB_SECRETO')][:25]"
  echo "$RES" | python3 -c 'import json,sys;r=json.load(sys.stdin);print("  detalle:", (r.get("detalle") or "")[:600])'
  revisar $M
done
caso "{\"auto\":\"$N\",\"equipo\":\"srv-prueba-rhel9-01\",\"params\":{},\"min\":25,\"caso\":\"C2\"}" > /dev/null
echo "== C2 repetir srv-prueba-rhel9-01: $(estado) $(ticket)"
jq_ "{k: {x: y for x, y in v.items() if x in ('CAMBIOS','COMMIT','SECRETOS','SECRETOS_BOVEDA')} for k, v in (s.get('faceops_inventario_git') or {}).items()}"
jq_ "[x for v in (s.get('faceops_inventario_git') or {}).values() for x in v.get('archivo', []) if x.startswith('ART_')]"
