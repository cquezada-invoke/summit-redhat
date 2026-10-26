# Grupo 'Automatización Ansible', campos u_tiempo_manual_estimado / u_job_aap en change_task y prueba de ciclo de vida.
import sys; sys.path.insert(0, '.')
from sn_lib import sn
import json,time
def one(path):
    s,j=sn("GET",path); r=j.get("result",[]) if isinstance(j,dict) else []; return r[0] if r else None
# grupo
g=one("/api/now/table/sys_user_group?sysparm_query=name=Automatización Ansible&sysparm_fields=sys_id")
if not g:
    s,j=sn("POST","/api/now/table/sys_user_group",{"name":"Automatización Ansible","description":"Robots de Ansible Automation Platform: cada tarea automatizada queda registrada aquí"}); print("crear grupo",s); g=j["result"]
gid=g["sys_id"]; uid="a45766dfc3001310bfc1be13e4013193"
m=one(f"/api/now/table/sys_user_grmember?sysparm_query=group={gid}^user={uid}&sysparm_fields=sys_id")
if not m: s,_=sn("POST","/api/now/table/sys_user_grmember",{"group":gid,"user":uid}); print("miembro",s)
print("grupo",gid)
# campos
for el,typ,lab,extra in [("u_tiempo_manual_estimado","integer","Tiempo manual estimado (min)",{}),("u_job_aap","url","Job de AAP",{"max_length":"1024"})]:
    f=one(f"/api/now/table/sys_dictionary?sysparm_query=name=change_task^element={el}&sysparm_fields=sys_id")
    if not f:
        b={"name":"change_task","element":el,"internal_type":typ,"column_label":lab,"active":"true"}; b.update(extra)
        s,j=sn("POST","/api/now/table/sys_dictionary",b); print("campo",el,s, (j.get("result",{}).get("sys_id") if isinstance(j,dict) and "result" in j else j))
    else: print("campo ya existe",el)
time.sleep(3)
# prueba ciclo de vida con modelo Change Registration
reg="e55d0bfec343101035ae3f52c1d3ae49"  # modelo Standard
s,j=sn("POST","/api/now/table/change_request",{"chg_model":reg,"short_description":"PRUEBA ciclo de vida (se cancela)","assignment_group":gid,"assigned_to":uid,"state":"-1"})
print("CR",s,j["result"]["number"] if s==201 else j); cr=j["result"]["sys_id"]; print("estado CR",j["result"]["state"])
s,j=sn("POST","/api/now/table/change_task",{"change_request":cr,"short_description":"PRUEBA tarea","assignment_group":gid,"assigned_to":uid,"state":"2","u_tiempo_manual_estimado":"240"})
print("CTASK",s,j["result"]["number"] if s==201 else j); ct=j["result"]["sys_id"] if s==201 else None
if ct:
    s,j=sn("PATCH",f"/api/now/table/change_task/{ct}",{"state":"3","close_code":"successful","close_notes":"ok","work_notes":"prueba","u_job_aap":"https://34.176.42.56/execution/jobs/playbook/1/output"})
    print("cerrar ctask",s,j["result"]["state"] if s==200 else j, j["result"].get("u_tiempo_manual_estimado") if s==200 else "")
for st in ["0","3"]:
    s,j=sn("PATCH",f"/api/now/table/change_request/{cr}",{"state":st,"close_code":"successful","close_notes":"prueba"})
    print("CR ->",st,s,j["result"]["state"] if s==200 else j)
