def accion_visitante(f):
    import random
    c = cfg()
    if f.get("accion") == "crear" and f.get("nombre") and f.get("correo"):
        n = 1
        while f"visita{n:02d}" in c["usuarios"]:
            n += 1
        usuario = f"visita{n:02d}"
        clave = random.choice(["Ansible", "RedHat", "Summit", "Invoke"]) + "-" + str(random.randint(1000, 9999))
        if ejecutar_visitante(usuario, "present", clave):
            c = cfg()
            c["usuarios"][usuario] = {"nombre": f["nombre"][:60], "correo": f["correo"][:120], "visitante": True}
            guardar_cfg(c)
            return pagina_visitante(cred=(usuario, clave, f["nombre"]))
        return pagina_visitante("No se pudo crear la cuenta, revisa el log del panel")
    if f.get("accion") == "quitar" and c["usuarios"].get(f.get("usuario"), {}).get("visitante"):
        threading.Thread(target=ejecutar_visitante, args=(f["usuario"], "absent"), daemon=True).start()
        c = cfg()
        c["usuarios"].pop(f["usuario"], None)
        guardar_cfg(c)
        return pagina_visitante(f"Cuenta {f['usuario']} eliminada")
    return pagina_visitante()


def ejecutar_visitante(usuario, estado, clave=""):
    CFG = cfg()
    host = CFG.get("nodo_visitante", "nodo-so")
    if estado == "present":
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", host,
                            f"sudo useradd -m -G visitantes {usuario} && echo '{usuario}:{clave}' | sudo chpasswd"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            print(r.stderr[-2000:], flush=True)
        return r.returncode == 0
    with locks_host.setdefault(host, threading.Lock()):
        r = subprocess.run(["ansible-playbook", "-i", CFG["inventario"], "visitante.yml", "-l", host,
                            "-e", json.dumps({"usuario": usuario, "clave": clave, "estado": estado})],
                           cwd=CFG["directorio_playbooks"], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:], flush=True)
    return r.returncode == 0


def pagina_visitante(msg="", cred=None):
    from urllib.parse import urlparse
    c = cfg()
    ip = urlparse(c["url_publica"]).hostname
    puerto = c.get("puerto_ssh_visitante", 2222)
    nodo = c.get("nodo_visitante", "nodo-so")
    vis = sorted((u, d) for u, d in c["usuarios"].items() if d.get("visitante"))
    filas = "".join(f"<tr><td>{html.escape(u)}</td><td>{html.escape(d['nombre'])}</td><td>{html.escape(d['correo'])}</td>"
                    f"<td><form method='post' style='margin:0'><input type='hidden' name='accion' value='quitar'>"
                    f"<input type='hidden' name='usuario' value='{html.escape(u)}'><button>Quitar acceso</button></form></td></tr>" for u, d in vis)
    caja = ""
    if cred:
        u, k, nombre = cred
        caja = f"""<div style="border:2px solid var(--ok);border-radius:10px;padding:18px;margin:16px 0;background:var(--card)">
<h2 style="margin-top:0">Listo, {html.escape(nombre)}: tu acceso a {nodo}</h2>
<p style="font-size:20px">Comando: <code style="font-size:20px">ssh -p {puerto} {u}@{ip}</code></p>
<p style="font-size:20px">Usuario: <b>{u}</b> &nbsp; Clave: <b>{html.escape(k)}</b></p>
<p class="mut">Una vez dentro, prueba un cambio “a mano”, por ejemplo:</p>
<pre>sudo sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
echo 'yo ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/atajo
sudo vi /etc/ssh/sshd_config</pre>
<p>Ansible lo detecta, lo revierte en segundos y te llega un correo con el detalle. Mira el <a href="/">panel</a>.</p></div>"""
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Visitantes</title>{ESTILO}</head><body><header><b>Prueba tú mismo · Visitantes</b><a href="/" style="color:#fff">← Volver al panel</a></header><main>
{f'<p class="badge restablecido">{html.escape(msg)}</p>' if msg else ''}{caja}
<h3>Crear acceso para un visitante</h3><p class="mut">Se crea una cuenta en {nodo} con clave inventada y sudo. Queda lista al instante.</p>
<form method="post"><input type="hidden" name="accion" value="crear"><input name="nombre" placeholder="Nombre" required style="width:220px">
<input name="correo" type="email" placeholder="correo" required style="width:260px"> <button>Crear acceso</button></form>
<h3>Visitantes activos</h3><table><tr><th>Usuario</th><th>Nombre</th><th>Correo</th><th></th></tr>{filas or '<tr><td colspan="4" class="mut">Sin visitantes.</td></tr>'}</table>
</main></body></html>"""
