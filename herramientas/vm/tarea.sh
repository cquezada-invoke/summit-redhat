#!/bin/bash
# Plan «Nube · Validar conexión e inventariar» (C1–C3) sobre nubes/2, certificación y ticket real de validación.
source ~/summit-redhat/herramientas/pruebas/comun.sh
N="Nube · Validar conexión e inventariar"
pagina() { sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - 2>&1 <<'Q' | grep '^PAG'
import re
from app import create_app
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for u in ["/lab/nubes/2"] + [f"/lab/hipervisores/{i}" for i in range(1, 12)]:
        r = c.get(u)
        if r.status_code == 404 and "hipervisores" in u:
            continue
        h = r.get_data(as_text=True)
        sec = h[h.find('id="tickets-infra"'):] if 'id="tickets-infra"' in h else ""
        tit = re.search(r"<h2[^>]*>(Tickets de [^<]*)</h2>", sec)
        n = len(re.findall(r'href="/lab/tickets/\d+"|class="mono" href="[^"]*ticket', sec.split("</table>")[0]))
        prim = re.search(r'<a class="mono" href="[^"]*">([^<]+)</a>', sec)
        print("PAG", u, r.status_code, (tit.group(1) if tit else "SIN SECCIÓN"), "filas", n, "· primero", prim.group(1) if prim else "-",
              "· vacío" if 'id="tki-vacio">' in sec and 'hidden id="tki-vacio"' not in sec else "", "· pie" if sec and "lista_tickets" not in sec else "")
        if "hipervisores" in u:
            break
Q
}
fo() { sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 -u - "$@" 2>&1; }

echo "== Ficha tras el despliegue"; pagina

# Datos de la nube 2: fuente dinámica e inventario en AAP (mismo camino que la ficha) + huella de la conexión guardada
DATOS=$(fo <<'Q' | grep '^DATOS ' | tail -1 | cut -c7-
import json
from app import create_app
app = create_app()
with app.app_context():
    from flask import g
    from app.bd import q, q1
    from app.aap import AAP
    from app.infraestructuras import lista, estado_nube, _fuente
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    eqs = q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cl["id"])
    x = next(i for i in lista(cl["id"], eqs) if i["id"] == 2)
    aap = AAP.de_cliente(cl)
    inv_nom = x.get("inventario") or f"Infra · {x['nombre']}"
    inv = (aap.get("inventories/", name=inv_nom).get("results") or [None])[0]
    cred = (aap.get("credentials/", name=x["credencial"]).get("results") or [None])[0] if x.get("credencial") else None
    if inv and cred:
        _fuente(aap, inv, x["nombre"], x["tipo"], cred, dict(x["detalle_d"]))
    con = estado_nube(aap, dict(x, inventario=inv_nom), cl["id"], guardar=False)
    f = con.get("fuente") or {}
    print("DATOS", json.dumps({"nube": x["nombre"], "fuente": f.get("id"), "inv": (con.get("inv") or {}).get("id"),
          "cred": x.get("credencial"), "detalle": x.get("detalle"), "estado": x.get("estado"),
          "upd": f.get("job")}, ensure_ascii=False, default=str), flush=True)
Q
)
echo "DATOS $DATOS"
v() { echo "$DATOS" | python3 -c "import json,sys;print(json.load(sys.stdin).get('$1') or '')"; }
NUBE=$(v nube); FU=$(v fuente); INV=$(v inv)
[ -z "$FU" ] || [ -z "$INV" ] && { echo "ABORTA: sin fuente o inventario en AAP"; exit 1; }
upd_fuente() { sudo podman exec -i faceops python3 - "$FU" 2>/dev/null <<'Q' | tail -1
import sys
from app import create_app
create_app()
from app.bd import q1
from app.aap import AAP
cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
s = AAP.de_cliente(cl).get(f"inventory_sources/{sys.argv[1]}/")
print((s.get("summary_fields", {}).get("last_job") or {}).get("id", ""), s.get("status"))
Q
}
conexion() { sudo podman exec -i faceops python3 - 2>/dev/null <<'Q' | tail -1
from app import create_app
create_app()
from app.bd import q1
x = q1("SELECT credencial, detalle, inventario FROM infraestructuras WHERE id=2")
import hashlib, json; print(hashlib.sha1(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:12])
Q
}
P_='{"nv_nube":"'$NUBE'","nv_fuente_id":"'$FU'","nv_inventario_id":"'$INV'"}'

# C1: conexión guardada vigente (API de Compute Engine habilitada) y aprobar
caso "{\"auto\":\"$N\",\"params\":$P_,\"min\":15,\"caso\":\"C1\"}"
M1=$(jq_ "(s.get('faceops_nube') or {}).get('maquinas','')"); E1=$(jq_ "(s.get('faceops_nube') or {}).get('estado','')")
VMS=$(fo <<Q | grep '^VMS' | tail -1
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app.aap import AAP
    from app.bd import q
    from app.infraestructuras import maquinas_nube, estado_nube, lista
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1")
    aap = AAP.de_cliente(cl)
    x = next(i for i in lista(cl["id"], q("SELECT * FROM equipos WHERE cliente_id=? AND estado!='retirado'", cl["id"])) if i["id"] == 2)
    con = estado_nube(aap, x, cl["id"], guardar=True, ttl=0)
    hs = maquinas_nube(aap, $INV)
    print("VMS", "ficha=" + con["estado"], len(hs), ", ".join(f"{h['nombre']}({h.get('estado','')})" for h in hs)[:400])
Q
)
echo "C1 stats: estado=$E1 maquinas=$M1 · $VMS"
if [ "$(estado)" = exitoso ] && [ "$E1" = conectada ] && echo "$VMS" | grep -q "ficha=conectada" && [ "${M1:-0}" -gt 0 ] 2>/dev/null; then anotar C1 "Validar conexión GCP vigente y aprobar" ok "ticket exitoso; sincronización ok; $M1 VM · $VMS"
else anotar C1 "Validar conexión GCP vigente y aprobar" falla "$(estado) $E1 $M1 $(jq_ "r.get('detalle')")"; fi

# C2: rechazar la aprobación → no corre la sincronización; la conexión guardada no cambia
H0=$(conexion); U0=$(upd_fuente)
RES=$(fo "$N" "$P_" <<'Q' | grep '^RES ' | tail -1 | cut -c5-
import json, sys, time
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app.operacion import actualizar_tickets, resolver_aprobacion, solicitar
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], sys.argv[1])
    t = solicitar(u, cl, a, "", json.loads(sys.argv[2]), "Plan de prueba C2 (rechazo) de «Nube · Validar conexión e inventariar»", prueba=True)
    print("TICKET", t["numero"], flush=True)
    hecho = False
    for _ in range(90):
        time.sleep(8)
        try: actualizar_tickets()
        except Exception: pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if x["estado"] == "pendiente_aprobacion" and not hecho:
            resolver_aprobacion(u, t["id"], False, "Rechazado por Claude: caso C2 del plan de prueba (regla de Cristian)"); hecho = True
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error"):
            break
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    print("RES", json.dumps({"ticket": t["numero"], "estado": x["estado"]}), flush=True)
Q
)
[ -z "$RES" ] && RES='{"estado":"sin respuesta"}'
sleep 20; H1=$(conexion); U1=$(upd_fuente)
echo "C2: $RES · conexión $H0→$H1 · última sincronización $U0→$U1"
if [ "$(estado)" = rechazado ] && [ "$H0" = "$H1" ] && [ "$U0" = "$U1" ]; then anotar C2 "Rechazar la aprobación" ok "rechazado; sin sincronización nueva ($U1); conexión sin cambios ($H1)"
else anotar C2 "Rechazar la aprobación" falla "$(estado) · $H0/$H1 · $U0/$U1"; fi

# C3: fuente dinámica inexistente → ticket fallido con el motivo; no se toca la nube
U0=$(upd_fuente)
caso "{\"auto\":\"$N\",\"params\":{\"nv_nube\":\"$NUBE\",\"nv_fuente_id\":\"999999\",\"nv_inventario_id\":\"$INV\"},\"min\":10,\"caso\":\"C3\"}"
U1=$(upd_fuente); D3=$(jq_ "(r.get('detalle') or '')[:300]")
echo "C3: $(estado) · $D3 · sincronización $U0→$U1"
if [ "$(estado)" = fallido ] && [ -n "$D3" ] && [ "$U0" = "$U1" ]; then anotar C3 "Fuente dinámica inexistente" ok "fallido: $D3"
else anotar C3 "Fuente dinámica inexistente" falla "$(estado) · $D3"; fi

echo "CASOS $CASOS"; echo "TKS $TKS"
if echo "$CASOS" | grep -q '"falla"'; then echo "NO SE CERTIFICA: hay casos fallidos"; certificar "$N" "C1–C3"; exit 0; fi
certificar "$N" "C1–C3"

# Ticket real de validación de nubes/2 (mismo camino que la ficha), aprobado con la regla de Cristian
fo "$N" "$P_" <<'Q' | grep -E '^(REAL|VMS)'
import json, sys, time
from app import create_app
app = create_app()
with app.app_context():
    from app.bd import q1
    from app.aap import AAP
    from app.operacion import actualizar_tickets, resolver_aprobacion, solicitar
    from app.infraestructuras import maquinas_nube, _CACHE_NUBE
    cl = q1("SELECT * FROM clientes ORDER BY id LIMIT 1"); u = q1("SELECT * FROM usuarios WHERE id=1")
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], sys.argv[1])
    p = json.loads(sys.argv[2])
    t = solicitar(u, cl, a, "", p, f"Validar conexión e inventariar {p['nv_nube']}")
    hecho = False
    for _ in range(110):
        time.sleep(8)
        try: actualizar_tickets()
        except Exception: pass
        x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if x["estado"] == "pendiente_aprobacion" and not hecho:
            resolver_aprobacion(u, t["id"], True, "Aprobado por Claude (regla de Cristian)"); hecho = True
        if x["estado"] in ("exitoso", "fallido", "rechazado", "vencido", "cancelado", "error"):
            break
    x = q1("SELECT * FROM tickets WHERE id=?", t["id"])
    _CACHE_NUBE.pop((cl["id"], 2), None)
    hs = maquinas_nube(AAP.de_cliente(cl), int(p["nv_inventario_id"]))
    print("REAL", t["numero"], x["estado"], (x["resultado"] or "")[-300:].replace("\n", " "), flush=True)
    print("VMS", len(hs), ", ".join(f"{h['nombre']}({h.get('estado','')})" for h in hs)[:600], flush=True)
Q

echo "== Ficha al final"; pagina
