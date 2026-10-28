#!/usr/bin/python3
"""Herramienta del data center: crea y consulta el parque a partir de inventario/<dc>.yml.

  dc.py redes      dc1   redes libvirt por zona (NAT, DHCP con IP fija por equipo)
  dc.py equipos    dc1   crea las VMs que falten (RHEL por imagen base + cloud-init; Windows desatendido)
  dc.py inventario dc1   genera el inventario de Ansible (~/datacenter/dc1.ini) y /etc/hosts, ~/.ssh/config
  dc.py publicar   dc1   publica las consolas web por nodo-nginx (puerto 8090 del stand)
  dc.py estado     dc1   tabla con el estado de cada equipo
  dc.py secretos   dc1   genera las claves del DC que falten (no las imprime)
  dc.py todo       dc1   redes + equipos + inventario + publicar

Idempotente: lo que ya existe no se toca. Claves en ~/datacenter/secretos-<dc>.txt (no van a Git, no se imprimen).
"""
import os, secrets, string, subprocess, sys, tempfile, textwrap

import yaml

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = "/var/lib/libvirt/images"
BASE_RHEL = f"{IMG}/rhel9-base.qcow2"
ISO_WIN = f"{IMG}/windows/ws2022-eval.iso"
LOCAL = os.path.expanduser("~/datacenter")
LLAVE = os.path.expanduser("~/.ssh/id_demo")


def sh(cmd, check=True, salida=False, entrada=None):
    r = subprocess.run(cmd, shell=True, text=True, input=entrada,
                       stdout=subprocess.PIPE if salida else None, stderr=subprocess.PIPE if salida else None)
    if check and r.returncode != 0:
        sys.exit(f"ERROR ({r.returncode}): {cmd}\n{(r.stderr or '')[-800:]}")
    return (r.stdout or "").strip() if salida else r.returncode


def cargar(dc):
    d = yaml.safe_load(open(f"{RAIZ}/inventario/{dc}.yml"))
    base = d["dc"]["red_base"]
    dcn = int(base.split(".")[1])
    for e in d["equipos"]:
        z = d["zonas"][e["zona"]]["octeto"]
        e["ip_completa"] = f"{base}.{z}.{e['ip']}"
        e["mac"] = f"52:54:00:{dcn:02x}:{z:02x}:{e['ip']:02x}"
        e["red"] = f"{d['dc']['id']}-{e['zona']}"
        e["fqdn"] = (f"{e['nombre']}.{d['dc']['dominio_ad']}" if e["so"].startswith("windows")
                     else f"{e['nombre']}.{d['dc']['dominio_idm']}")
    return d


def secretos(dc):
    os.makedirs(LOCAL, mode=0o700, exist_ok=True)
    ruta = f"{LOCAL}/secretos-{dc}.txt"
    s = {}
    if os.path.exists(ruta):
        s = dict(l.rstrip("\n").split("=", 1) for l in open(ruta) if "=" in l)
    return ruta, s


def clave(dc, nombre):
    ruta, s = secretos(dc)
    if nombre not in s:
        alfabeto = string.ascii_letters + string.digits
        s[nombre] = "".join(secrets.choice(alfabeto) for _ in range(14)) + "Aa1!"
        with os.fdopen(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.writelines(f"{k}={v}\n" for k, v in s.items())
    return s[nombre]


# ---------------------------------------------------------------- redes
def redes(d):
    dc, base = d["dc"]["id"], d["dc"]["red_base"]
    existentes = sh("sudo virsh net-list --all --name", salida=True).split()
    for zona, z in d["zonas"].items():
        nombre, red = f"{dc}-{zona}", f"{base}.{z['octeto']}"
        if nombre not in existentes:
            hosts = "".join(f"<host mac='{e['mac']}' name='{e['nombre']}' ip='{e['ip_completa']}'/>"
                            for e in d["equipos"] if e["zona"] == zona)
            xml = f"""<network><name>{nombre}</name><forward mode='nat'/>
<bridge name='{dc}z{z['octeto']}' stp='on' delay='0'/>
<ip address='{red}.1' netmask='255.255.255.0'><dhcp><range start='{red}.100' end='{red}.199'/>{hosts}</dhcp></ip></network>"""
            with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
                f.write(xml)
            sh(f"sudo virsh net-define {f.name} >/dev/null && sudo virsh net-autostart {nombre} >/dev/null")
            os.unlink(f.name)
            print(f"· red {nombre} {red}.0/24 creada")
        else:
            for e in d["equipos"]:
                if e["zona"] == zona:
                    sh(f"sudo virsh net-update {nombre} add ip-dhcp-host \"<host mac='{e['mac']}' name='{e['nombre']}' "
                       f"ip='{e['ip_completa']}'/>\" --live --config >/dev/null 2>&1", check=False)
        if sh(f"sudo virsh net-info {nombre} | grep -q 'Active:.*yes'", check=False) != 0:
            sh(f"sudo virsh net-start {nombre} >/dev/null")
    print("· redes listas")


# ---------------------------------------------------------------- equipos
def existe_vm(nombre):
    return sh(f"sudo virsh dominfo {nombre} >/dev/null 2>&1", check=False) == 0


def crear_rhel(d, e):
    pub = open(LLAVE + ".pub").read().strip()
    disco = f"{IMG}/{e['nombre']}.qcow2"
    sh(f"sudo qemu-img create -q -f qcow2 -F qcow2 -b {BASE_RHEL} {disco} {e['disco_gb']}G")
    ud = textwrap.dedent(f"""\
        #cloud-config
        hostname: {e['nombre']}
        fqdn: {e['fqdn']}
        prefer_fqdn_over_hostname: true
        manage_etc_hosts: false
        users:
          - name: ansible
            sudo: ALL=(ALL) NOPASSWD:ALL
            shell: /bin/bash
            ssh_authorized_keys:
              - {pub}
        ssh_pwauth: false
        timezone: America/Santiago
        growpart: {{mode: auto, devices: ['/']}}
        """)
    with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
        f.write(ud)
    sh(f"sudo virt-install --name {e['nombre']} --memory {e['ram_mb']} --vcpus {e['cpu']} --import "
       f"--disk {disco},bus=virtio --osinfo linux2022 --network network={e['red']},mac={e['mac']},model=virtio "
       f"--graphics none --cloud-init user-data={f.name} --noautoconsole >/dev/null")
    os.unlink(f.name)


def crear_windows(d, e):
    dc = d["dc"]["id"]
    if not os.path.exists(ISO_WIN):
        sys.exit(f"Falta la ISO de Windows en {ISO_WIN} (la descarga demo5-remediacion/windows/instalar-windows.sh)")
    ca, cd = clave(dc, f"{e['nombre']}_ansible"), clave(dc, f"{e['nombre']}_administrador")
    plantilla = open(f"{RAIZ}/plantillas/autounattend-windows.xml").read()
    xml = (plantilla.replace("__NOMBRE__", e["nombre"].upper()[:15])
           .replace("__CLAVE_ADMIN__", cd).replace("__CLAVE_ANSIBLE__", ca))
    tmp = tempfile.mkdtemp()
    open(f"{tmp}/autounattend.xml", "w").write(xml)
    iso = f"{IMG}/windows/autounattend-{e['nombre']}.iso"
    sh(f"genisoimage -quiet -J -r -o /tmp/aut-{e['nombre']}.iso {tmp}/autounattend.xml && "
       f"sudo mv /tmp/aut-{e['nombre']}.iso {iso}")
    sh(f"rm -rf {tmp}")
    sh(f"sudo virt-install --name {e['nombre']} --memory {e['ram_mb']} --vcpus {e['cpu']} --os-variant win2k22 "
       f"--disk path={IMG}/{e['nombre']}.qcow2,size={e['disco_gb']},bus=sata,format=qcow2 "
       f"--cdrom {ISO_WIN} --disk path={iso},device=cdrom,bus=sata "
       f"--network network={e['red']},mac={e['mac']},model=e1000e --graphics vnc,listen=127.0.0.1 "
       f"--noautoconsole --boot hd,cdrom >/dev/null")


def equipos(d):
    for e in d["equipos"]:
        if existe_vm(e["nombre"]):
            print(f"· {e['nombre']} ya existe")
            continue
        (crear_windows if e["so"].startswith("windows") else crear_rhel)(d, e)
        sh(f"sudo virsh autostart {e['nombre']} >/dev/null")
        print(f"· {e['nombre']} creado ({e['so']}, {e['cpu']} vCPU, {e['ram_mb'] // 1024} GB, {e['ip_completa']})")


# ---------------------------------------------------------------- inventario
def inventario(d):
    dc = d["dc"]["id"]
    os.makedirs(LOCAL, mode=0o700, exist_ok=True)
    # /etc/hosts del anfitrión
    actual = open("/etc/hosts").read()
    nuevas = [f"{e['ip_completa']} {e['fqdn']} {e['nombre']}" for e in d["equipos"]
              if not any(l.split() and l.split()[-1] == e["nombre"] for l in actual.splitlines())]
    if nuevas:
        sh("sudo tee -a /etc/hosts >/dev/null", entrada="\n".join(nuevas) + "\n")
    # SSH a los equipos Linux con la llave de la demo
    cfg = os.path.expanduser("~/.ssh/config.demo")
    if f"Host {dc}-*" not in open(cfg).read():
        open(cfg, "a").write(f"\nHost {dc}-*\n  User ansible\n  IdentityFile ~/.ssh/id_demo\n  StrictHostKeyChecking accept-new\n")
    # Inventario de Ansible por rol
    grupos = {}
    for e in d["equipos"]:
        for r in e["roles"]:
            grupos.setdefault(r.replace("-", "_"), []).append(e["nombre"])
    lin = [f"# Generado por dc.py desde inventario/{dc}.yml — no editar a mano", f"[{dc}:children]", f"{dc}_linux", f"{dc}_windows", ""]
    lin += [f"[{dc}_linux]"] + [f"{e['nombre']} ansible_host={e['ip_completa']}" for e in d["equipos"] if e["so"] == "rhel9"] + [""]
    lin += [f"[{dc}_windows]"] + [f"{e['nombre']} ansible_host={e['ip_completa']}" for e in d["equipos"] if e["so"].startswith("windows")] + [""]
    for g, hs in sorted(grupos.items()):
        lin += [f"[{g}]"] + hs + [""]
    lin += [f"[{dc}:vars]", f"dc_id={dc}", ""]
    lin += [f"[{dc}_windows:vars]", "ansible_connection=winrm", "ansible_winrm_transport=ntlm", "ansible_port=5985",
            "ansible_winrm_server_cert_validation=ignore",
            "ansible_winrm_read_timeout_sec=180", "ansible_winrm_operation_timeout_sec=170", ""]
    open(f"{LOCAL}/{dc}.ini", "w").write("\n".join(lin))
    open(f"{RAIZ}/inventario/{dc}.ini", "w").write("\n".join(lin))  # copia que viaja al proyecto de AAP
    print(f"· inventario {LOCAL}/{dc}.ini ({len(d['equipos'])} equipos, {len(grupos)} roles)")


# ---------------------------------------------------------------- publicar
def publicar(d):
    eq = {e["nombre"]: e for e in d["equipos"]}
    bloques = []
    for p in d.get("publicaciones", []):
        e, ph = eq[p["equipo"]], p["puerto_vm_host"]
        nombre = f"dc-{p['equipo']}-{p['puerto']}"
        sh(f"sudo tee /etc/systemd/system/{nombre}.socket >/dev/null", entrada=textwrap.dedent(f"""\
            [Unit]
            Description=Publicación de {p['ruta']} ({p['equipo']}:{p['puerto']}) hacia nodo-nginx
            [Socket]
            ListenStream=192.168.122.1:{ph}
            FreeBind=true
            [Install]
            WantedBy=sockets.target
            """))
        sh(f"sudo tee /etc/systemd/system/{nombre}.service >/dev/null", entrada=textwrap.dedent(f"""\
            [Unit]
            Description=Proxy {p['ruta']} → {e['ip_completa']}:{p['puerto']}
            [Service]
            ExecStart=/usr/lib/systemd/systemd-socket-proxyd {e['ip_completa']}:{p['puerto']}
            """))
        sh(f"sudo systemctl daemon-reload && sudo systemctl enable -q --now {nombre}.socket")
        sh(f"sudo firewall-cmd -q --zone=libvirt --query-port={ph}/tcp || sudo firewall-cmd -q --zone=libvirt --add-port={ph}/tcp", check=False)
        sh(f"sudo firewall-cmd -q --permanent --zone=libvirt --query-port={ph}/tcp || sudo firewall-cmd -q --permanent --zone=libvirt --add-port={ph}/tcp", check=False)
        bloques.append(textwrap.dedent(f"""\
            location {p['ruta']} {{
                proxy_pass http://192.168.122.1:{ph};
                proxy_set_header Host $http_host;
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_set_header X-Forwarded-Host $http_host;
                proxy_set_header X-Forwarded-Port 8090;
                proxy_buffer_size 128k;
                proxy_buffers 4 256k;
                proxy_busy_buffers_size 256k;
                client_max_body_size 50m;
            }}
            """))
    conf = f"# {d['dc']['nombre']} — generado por datacenter/herramientas/dc.py\n" + "".join(bloques)
    sh(f"ssh nodo-nginx 'sudo tee /etc/nginx/default.d/{d['dc']['id']}.conf >/dev/null && sudo nginx -t -q && sudo systemctl reload nginx'",
       entrada=conf)
    print("· publicado en http://34.176.42.56:8090: " + ", ".join(p["ruta"] for p in d.get("publicaciones", [])))


# ---------------------------------------------------------------- secretos
CLAVES_DC = ["ad_dsrm", "ad_svc_midpoint", "idm_admin", "idm_dm", "ds_dm", "ds_svc_lectura", "midpoint_admin",
             "midpoint_bd", "keycloak_admin", "clave_inicial_personas"]


def secretos_dc(d):
    for k in CLAVES_DC:
        clave(d["dc"]["id"], k)
    print(f"· secretos listos en {LOCAL}/secretos-{d['dc']['id']}.txt ({len(secretos(d['dc']['id'])[1])} claves)")


# ---------------------------------------------------------------- estado
def estado(d):
    print(f"{d['dc']['nombre']}")
    print(f"{'equipo':<12} {'so':<12} {'ip':<14} {'vm':<10} {'acceso':<8} roles")
    for e in d["equipos"]:
        vm = sh(f"sudo virsh domstate {e['nombre']} 2>/dev/null", salida=True, check=False) or "no existe"
        if e["so"].startswith("windows"):
            ok = sh(f"timeout 3 bash -c '</dev/tcp/{e['ip_completa']}/5985' 2>/dev/null", check=False) == 0
        else:
            ok = sh(f"ssh -o ConnectTimeout=3 -o BatchMode=yes {e['nombre']} true 2>/dev/null", check=False) == 0
        print(f"{e['nombre']:<12} {e['so']:<12} {e['ip_completa']:<14} {vm:<10} {'OK' if ok else '—':<8} {', '.join(e['roles'])}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    accion, d = sys.argv[1], cargar(sys.argv[2])
    pasos = {"redes": [redes], "equipos": [equipos], "inventario": [inventario], "publicar": [publicar],
             "estado": [estado], "secretos": [secretos_dc], "todo": [redes, equipos, inventario, secretos_dc, publicar]}
    for f in pasos.get(accion) or sys.exit(__doc__):
        f(d)
