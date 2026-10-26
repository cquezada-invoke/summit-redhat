from sn_lib import sn
def one(p):
    s,j=sn("GET",p); r=j.get("result",[]) if isinstance(j,dict) else []; return r[0] if r else None
for el,typ,lab,extra in [("u_tipo_tarea","string","Tipo de tarea automatizada",{"max_length":"40"}),("u_duracion_real_s","decimal","Duración real (s)",{})]:
    if not one(f"/api/now/table/sys_dictionary?sysparm_query=name=change_task^element={el}&sysparm_fields=sys_id"):
        b={"name":"change_task","element":el,"internal_type":typ,"column_label":lab,"active":"true"}; b.update(extra)
        s,j=sn("POST","/api/now/table/sys_dictionary",b); print("campo",el,s)
g=one("/api/now/table/sys_user_group?sysparm_query=name=Automatización Ansible&sysparm_fields=sys_id")["sys_id"]
filt=f"assignment_group={g}^ORDERBYDESCnumber"
reps=[
 {"title":"Tareas automatizadas por Ansible","table":"change_task","type":"list","filter":filt,
  "field_list":"number,change_request,short_description,u_tipo_tarea,state,u_duracion_real_s,u_tiempo_manual_estimado,work_start,work_end,u_job_aap","is_published":"true","description":"Cada operación de plataforma ejecutada por Ansible: duración real vs. tiempo manual estimado."},
 {"title":"Tareas automatizadas por tipo","table":"change_task","type":"bar","filter":f"assignment_group={g}","field":"u_tipo_tarea","aggregate":"COUNT","is_published":"true","description":"Cantidad de tareas automatizadas por tipo."},
 {"title":"Tiempo manual estimado vs. real por tipo (Ansible)","table":"change_task","type":"list","filter":f"assignment_group={g}^state=3","field_list":"u_tipo_tarea,short_description,u_duracion_real_s,u_tiempo_manual_estimado","is_published":"true","description":"Comparación por tarea: segundos reales vs. minutos que tomaría por ticket manual."},
]
for r in reps:
    ex=one(f"/api/now/table/sys_report?sysparm_query=title={r['title']}&sysparm_fields=sys_id")
    if ex: s,j=sn("PATCH",f"/api/now/table/sys_report/{ex['sys_id']}",r); sid=ex["sys_id"]
    else: s,j=sn("POST","/api/now/table/sys_report",r); sid=j.get("result",{}).get("sys_id") if isinstance(j,dict) else None
    print(r["title"],s,sid)
print("grupo",g)
