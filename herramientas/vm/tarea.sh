cd ~/summit-redhat && git log --oneline -1 | cut -c1-50
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -40
import re, collections
from app import create_app
from app.bd import q, q1
app = create_app()
with app.app_context():
    for n in ("Estaciones · Limpieza de disco", "Estaciones · Cumplimiento CIS", "Windows · Aplicar actualizaciones"):
        a = q1("SELECT id, visible FROM automatizaciones WHERE cliente_id=1 AND nombre_aap=?", n)
        print("AUTO", n, "->", dict(a) if a else "NO SINCRONIZADA")
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1; s["csrf"] = "x"
    for eid in (15,):
        h = c.get(f"/faceops/lab/inventario/{eid}").get_data(as_text=True)
        i = h.find('id="hb-dlg"'); t = h[i:h.find("</dialog>", i)]
        filas = re.findall(r'<tr class="hb-it( bloq)?".*?<b title="([^"]+)".*?hb-tag (dif|qw).*?</tr>', t, re.S)
        mot = re.findall(r'Bloqueada: ([^<]+)</div>', t)
        print("EQ", eid, "skills", len(filas), "habilitadas", sum(1 for f in filas if not f[0]))
        print(" motivos", dict(collections.Counter(mot)))
        print(" nombres", [f[1] for f in filas][:20])
    h = c.get("/faceops/lab/inventario/1").get_data(as_text=True)
    print("servidor 1 sigue con skills:", h.count('class="hb-it'))
Q
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | grep -v -i warn | tail -20
import collections
from flask import g
from app import create_app
from app.bd import q1
app = create_app()
with app.test_request_context("/faceops/lab/estaciones"):
    from app.habilidades import habilidades_maquina
    g.cliente = dict(q1("SELECT * FROM clientes WHERE id=1")); g.usuario = dict(q1("SELECT * FROM usuarios WHERE id=1")); g.perfil = {"modo": "escritura"}
    e = dict(q1("SELECT * FROM equipos WHERE id=15"))
    for so in ("Red Hat Enterprise Linux 9.6", "Ubuntu 24.04", "Windows 11 Pro", "Windows 10 Enterprise", "macOS 15 Sequoia"):
        hs = habilidades_maquina(dict(e, so=so))
        print(so, "| total", len(hs), "| dif", sum(h["tipo"] == "diferenciador" for h in hs), "| habilitadas", [h["nombre"] for h in hs if not h["bloqueo"]],
              "|", dict(collections.Counter(h["bloqueo"] for h in hs)))
Q
