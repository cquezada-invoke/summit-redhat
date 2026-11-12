sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'Q' 2>&1 | tail -60
from app import create_app
from app.bd import q
app = create_app()
with app.app_context():
    for e in q("SELECT id, cliente_id, nombre, categoria, so, estado, ip, ambiente, ubicacion, substr(datos,1,300) d FROM equipos WHERE categoria NOT IN ('servidor','router','switch','firewall','red','balanceador','almacenamiento','plataforma','clúster openshift')"):
        print("EQ", dict(e))
    from app.pruebas import estado
    for n in ["Seguridad · Aplicar parches de seguridad","Remediación · Limpiar logs","Seguridad · Escaneo de servidores","Plataforma · Controlar servicio","Sistema · Paquetes · inventario y respaldo","Licencias · Levantar desde las plataformas","Drift · Windows","Portal · Windows · Informe de seguridad","Recuperación · Respaldar volúmenes con restic","Certificados · Emitir para servicio","Sistema · Inventariar máquina","Windows · Habilitar escritorio remoto"]:
        for a in q("SELECT * FROM automatizaciones WHERE nombre_aap=? AND cliente_id=1", n):
            a = dict(a)
            try: ep = estado(a)["estado"]
            except Exception as x: ep = "ERR " + str(x)[:80]
            print("AUTO", n, "| visible", a.get("visible"), "| so", a.get("so") if "so" in a else "", "| prueba", ep, "| playbook", a.get("playbook"))
Q
