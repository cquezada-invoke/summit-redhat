for i in $(seq 1 40); do sudo podman exec faceops true 2>/dev/null && break; sleep 6; done
cat > /tmp/val.py <<'PY'
import time, re, json
from app import create_app
from app.bd import q, q1, j, ej
app = create_app()
from app import pruebas
from app.aap import AAP
from app.operacion import sincronizar_catalogo, solicitar, resolver_aprobacion, actualizar_tickets
from app.infraestructuras import estado_nube, lista, _fuente, _CACHE_NUBE
N = "Nube · Validar conexión e inventariar"
with app.app_context():
    cl = q1("SELECT * FROM clientes WHERE codigo='lab'")
    u = q1("SELECT * FROM usuarios WHERE usuario='cquezada'") or q1("SELECT * FROM usuarios WHERE es_admin=1 ORDER BY id LIMIT 1")
    x = next(i for i in lista(cl["id"], []) if i["id"] == 2)
    print("NUBE", x["nombre"], "| estado BD:", x["estado"], "| cred:", x["credencial"], "| inv:", x["inventario"], "| verif:", x["verificado"])
    print("DETALLE sin secretos:", not re.search(r"private_key|BEGIN", x["detalle"] or ""))
    aap = AAP.de_cliente(cl)
    c = (aap.get("credentials/", name=x["credencial"]).get("results") or [None])[0] if x["credencial"] else None
    if c:
        ins = c.get("inputs") or {}
        print("CRED AAP id", c["id"], "modificada", c.get("modified"), "| email:", bool(ins.get("username")), "| proyecto:", ins.get("project"), "| llave cifrada:", ins.get("ssh_key_data") == "$encrypted$")
    else:
        print("CRED AAP: NO EXISTE")
    print("AUD", [(r["fecha"] if "fecha" in r.keys() else "", r["accion"], r["objeto"] if "objeto" in r.keys() else "") for r in q("SELECT * FROM auditoria WHERE cliente_id=? AND (accion LIKE '%infraestructura%') ORDER BY id DESC LIMIT 4", cl["id"])])
    print("CATALOGO", sincronizar_catalogo(cl, "cquezada"))
    a = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cl["id"], N)
    print("AUTO", bool(a), pruebas.estado(a)["estado"] if a else "-")
    if not (a and c):
        raise SystemExit("sin acción o sin credencial")
    inv_nom = x["inventario"] or f"Infra · {x['nombre']}"
    inv = (aap.get("inventories/", name=inv_nom).get("results") or [None])[0]
    if not inv:
        org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
        inv = aap.post("inventories/", {"name": inv_nom, "organization": org, "description": f"Máquinas de {x['nombre']}"})
        ej("UPDATE infraestructuras SET inventario=? WHERE id=?", inv_nom, x["id"])
    _fuente(aap, inv, x["nombre"], x["tipo"], c, dict(x["detalle_d"]))
    _CACHE_NUBE.clear()
    con = estado_nube(aap, dict(x, inventario=inv_nom), cl["id"], guardar=False)
    P = {"nv_nube": x["nombre"], "nv_fuente_id": con["fuente"]["id"], "nv_inventario_id": con["inv"]["id"]}
    def correr(params, aprobar, motivo, prueba=True):
        t = solicitar(u, cl, a, "", params, motivo, prueba=prueba)
        for _ in range(120):
            try: actualizar_tickets()
            except Exception as e_: print("  act:", str(e_)[:100])
            tt = q1("SELECT * FROM tickets WHERE id=?", t["id"])
            if tt["estado"] == "pendiente_aprobacion" and aprobar is not None:
                resolver_aprobacion(u, t["id"], aprobar, "Aprobado por Claude (regla de Cristian)" if aprobar else "Rechazo de prueba (plan C2)")
            if tt["estado"] in ("exitoso", "fallido", "rechazado", "cancelado"):
                break
            time.sleep(8)
        tt = dict(q1("SELECT * FROM tickets WHERE id=?", t["id"]))
        txt = " ".join(str(tt.get(k) or "") for k in ("resultado", "mensaje", "error", "salida") if k in tt)
        print("TICKET", tt["numero"], tt["estado"], re.sub(r"\s+", " ", txt)[-220:])
        return tt
    r1 = correr(P, True, f"Plan de prueba C1: validar {x['nombre']}")
    r2 = correr(P, False, "Plan de prueba C2: rechazar")
    r3 = correr(dict(P, nv_fuente_id=999999), True, "Plan de prueba C3: fuente inexistente")
    ok = r1["estado"] == "exitoso" and r2["estado"] == "rechazado" and r3["estado"] == "fallido"
    if ok:
        _CACHE_NUBE.clear()
        con2 = estado_nube(aap, dict(x, inventario=inv_nom), cl["id"])
        pruebas.certificar(cl["id"], N, "datos/planes-prueba.yml · Nube · Validar conexión e inventariar (C1-C3)",
            [{"id": "C1", "resultado": "ok", "detalle": f"{r1['numero']}: fuente sincronizada; {con2.get('hosts')} VM; nube {con2.get('estado')}"},
             {"id": "C2", "resultado": "ok", "detalle": f"{r2['numero']}: rechazada, no corre"},
             {"id": "C3", "resultado": "ok", "detalle": f"{r3['numero']}: fallido con motivo; nada tocado"}],
            [r1["numero"], r2["numero"], r3["numero"]], "aprobada", "cquezada", "Plan completo de la nueva acción de validación de nubes")
        print("CERT", pruebas.estado(a)["estado"])
        rr = correr(P, True, f"Validar conexión e inventariar {x['nombre']}", prueba=False)
        print("TICKET_REAL", rr["numero"], rr["estado"])
        _CACHE_NUBE.clear()
        con3 = estado_nube(aap, dict(x, inventario=inv_nom), cl["id"])
        hs = aap.get(f"inventories/{con3['inv']['id']}/hosts/", page_size=50).get("results", [])
        print("NUBE_ESTADO", con3.get("estado"), "| VMs:", len(hs), [h_["name"] for h_ in hs])
    else:
        print("PLAN NO PASA; sin certificar", r1["estado"], r2["estado"], r3["estado"])
    c_ = app.test_client()
    with c_.session_transaction() as s: s["uid"] = u["id"]; s["csrf"] = "x" * 32
    r = c_.get("/lab/nubes/2"); h = r.get_data(as_text=True)
    print("PAGINA", r.status_code, "barra", "sl-barra" in h, "encendido", 'id="nb-energia"' in h, "cajon", 'id="cx-cajon"' in h, "form", "/conexion" in h,
          "secretos", bool(re.search(r"private_key|BEGIN [A-Z ]*PRIVATE", h)), "| validar_no:", re.findall(r'<b style="color:var\(--bad\)">([^<]+)', h)[:1],
          "| estado:", re.findall(r'eq-energia [^"]*"><i></i><div><b>([^<]+)', h))
    print("LINK cuenta", re.findall(r'href="(https://console\.cloud\.google\.com/iam-admin/[^"]+)"', h))
PY
sudo podman cp /tmp/val.py faceops:/tmp/val.py && sudo podman exec -w /opt/faceops -e PYTHONPATH=/opt/faceops -e FACEOPS_VIGIA=0 faceops timeout 3000 python3 /tmp/val.py 2>&1 | grep -v -i "warn" | tail -40
echo "--- LOGS"; sudo podman logs --since 4h faceops 2>&1 | grep -E "POST /lab/(nubes|infraestructuras)/2|Traceback" | tail -10
