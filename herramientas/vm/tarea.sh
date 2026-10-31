true
sudo podman exec -i -e FACEOPS_VIGIA=0 faceops python3 - <<'PY'
import re, time
from werkzeug.security import generate_password_hash
from app import create_app
app=create_app(); c=app.test_client()
with app.app_context():
    from app.bd import ej, q1
    ej("DELETE FROM usuarios WHERE usuario='prueba.act'")
    tid=ej("INSERT INTO usuarios(usuario,nombre,clave_hash,es_admin,activo) VALUES ('prueba.act','Prueba',?,1,1)", generate_password_hash("clave-de-prueba-123"))
with c.session_transaction() as s: s["uid"]=1; s["usuario_id"]=1; s["csrf"]="x"
r=c.post(f"/admin/usuarios/{tid}", data={"_csrf":"x","accion":"invitacion"}); h=r.get_data(as_text=True)
m=re.search(r'https://[^<"]+/ingresar/activar/([\w-]+)', h); print("admin", r.status_code, bool(m), m.group(0).split("/activar")[0] if m else "")
tok=m.group(1)
c2=app.test_client()
with c2.session_transaction() as s: s["csrf"]="y"
r=c2.post(f"/ingresar/activar/{tok}", data={"_csrf":"y","accion":"credenciales","usuario":"prueba.act","clave":"clave-de-prueba-123"})
h=r.get_data(as_text=True); sec=re.search(r'user-select:all;word-break:break-all">(\w+)<', h).group(1); print("paso1", r.status_code, "QR" if 'id="qr"' in h else "-")
r=c2.post(f"/ingresar/activar/{tok}", data={"_csrf":"y","accion":"codigo","codigo":__import__("app.doble_factor",fromlist=["x"])._codigo(sec,int(time.time()//30))}); print("paso2", r.status_code, r.headers.get("Location"))
with app.app_context():
    print(dict(q1("SELECT totp_activo, invita_hash FROM usuarios WHERE id=?", tid)))
    r=c.get(f"/ingresar/activar/{tok}"); print("reuso", r.status_code, r.headers.get("Location"))
    ej("DELETE FROM usuarios WHERE id=?", tid)
PY
curl -sk -o /dev/null -w "publico %{http_code}\n" https://127.0.0.1:8843/faceops/ingresar/activar/xx -H "Host: faceops.34.176.42.56.sslip.io"
