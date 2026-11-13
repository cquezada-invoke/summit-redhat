"""Alta manual de equipos que ya existen en la infraestructura (no los creó FaceOps): servidores, periféricos de red y storage.
Cada marca se integra distinto (SSH, API REST con token, NETCONF, WinRM…): el catálogo dice qué pide el formulario.
El secreto va a la bóveda (Vault) por AAP: FaceOps nunca toca máquinas ni secretos directo. El alta deja en AAP el host con
sus variables de conexión (sin secretos), en FaceOps el equipo «inventariado» (en gris hasta que una recolección lo deje
«con datos») y un ticket de «FaceOps · Guardar credencial en la bóveda» (con aprobación): la credencial viaja solo a AAP
(enmascarada en FaceOps) y la escribe ese trabajo con su token acotado a faceops/maquinas/*."""
import json
import re

from .bd import ahora, auditar, ej, q1

# método de autenticación → (etiqueta, campo secreto)
AUTENTICACION = {
    "clave": ("Usuario y clave", "clave"),
    "llave": ("Usuario y llave SSH privada", "llave_privada"),
    "token": ("Token o API key", "token"),
    "certificado": ("Certificado de cliente (PEM)", "certificado"),
    "kubeconfig": ("Kubeconfig o token de cuenta de servicio", "token"),
    "s3": ("Access key y secret key", "clave"),
}

# (clave, marca/plataforma, modelos de ejemplo, conexión de Ansible, colección, puerto, autenticaciones, tipos de equipo)
RED = [
    ("cisco-ios", "Cisco IOS / IOS-XE", "Catalyst 9000, ISR 4000, ASR 1000", "network_cli", "cisco.ios", 22, ["clave", "llave"], ["router", "switch"]),
    ("cisco-nxos", "Cisco NX-OS", "Nexus 9000, Nexus 3000", "network_cli", "cisco.nxos", 22, ["clave", "llave"], ["switch"]),
    ("cisco-iosxr", "Cisco IOS-XR", "ASR 9000, NCS 5500", "network_cli", "cisco.iosxr", 22, ["clave", "llave"], ["router"]),
    ("cisco-asa", "Cisco ASA / Firepower", "ASA 5500-X, Firepower 2100", "network_cli", "cisco.asa", 22, ["clave"], ["firewall"]),
    ("cisco-meraki", "Cisco Meraki", "MX, MS, MR (gestión en la nube)", "httpapi", "cisco.meraki", 443, ["token"], ["router", "switch", "firewall"]),
    ("juniper", "Juniper Junos", "MX, EX, QFX, SRX", "netconf", "junipernetworks.junos", 830, ["clave", "llave"], ["router", "switch", "firewall"]),
    ("arista", "Arista EOS", "7050X, 7280R", "httpapi", "arista.eos", 443, ["clave"], ["switch", "router"]),
    ("fortinet", "Fortinet FortiGate", "FortiGate 60F a 3000F", "httpapi", "fortinet.fortios", 443, ["token", "clave"], ["firewall", "router"]),
    ("paloalto", "Palo Alto PAN-OS", "PA-400, PA-3400, VM-Series", "httpapi", "paloaltonetworks.panos", 443, ["token", "clave"], ["firewall"]),
    ("checkpoint", "Check Point", "Quantum (vía servidor de gestión)", "httpapi", "check_point.mgmt", 443, ["clave", "token"], ["firewall"]),
    ("f5", "F5 BIG-IP", "i-Series, VE", "httpapi", "f5networks.f5_bigip", 443, ["clave", "token"], ["balanceador"]),
    ("citrix", "NetScaler (Citrix ADC)", "MPX, VPX", "httpapi", "netscaler.adc", 443, ["clave"], ["balanceador"]),
    ("haproxy", "HAProxy / NGINX en Linux", "Servidor Linux que balancea", "ssh", "ansible.builtin", 22, ["llave", "clave"], ["balanceador"]),
    ("aruba", "HPE Aruba AOS-CX", "CX 6300, CX 8325", "httpapi", "arubanetworks.aoscx", 443, ["clave"], ["switch"]),
    ("huawei", "Huawei VRP", "CloudEngine, NetEngine", "network_cli", "community.network", 22, ["clave"], ["router", "switch"]),
    ("dell-os10", "Dell SmartFabric OS10", "S5200, Z9000", "network_cli", "dellemc.os10", 22, ["clave"], ["switch"]),
    ("mikrotik", "MikroTik RouterOS", "CCR, CRS", "network_cli", "community.routeros", 22, ["clave", "llave"], ["router", "switch"]),
    ("vyos", "VyOS", "VyOS 1.4 / 1.5", "network_cli", "vyos.vyos", 22, ["clave", "llave"], ["router", "firewall"]),
    ("ubiquiti", "Ubiquiti EdgeOS", "EdgeRouter", "network_cli", "community.network", 22, ["clave"], ["router"]),
]
NETWORK_OS = {"cisco-ios": "cisco.ios.ios", "cisco-nxos": "cisco.nxos.nxos", "cisco-iosxr": "cisco.iosxr.iosxr", "cisco-asa": "cisco.asa.asa",
              "juniper": "junipernetworks.junos.junos", "arista": "arista.eos.eos", "fortinet": "fortinet.fortios.fortios",
              "aruba": "arubanetworks.aoscx.aoscx", "huawei": "community.network.ce", "dell-os10": "dellemc.os10.os10",
              "mikrotik": "community.routeros.routeros", "vyos": "vyos.vyos.vyos", "ubiquiti": "community.network.edgeos",
              "checkpoint": "check_point.mgmt.checkpoint", "f5": "f5networks.f5_bigip.bigip", "paloalto": "paloaltonetworks.panos.panos"}

STORAGE = [
    ("netapp-ontap", "NetApp ONTAP", "AFF, FAS, Cloud Volumes ONTAP", "httpapi", "netapp.ontap", 443, ["clave", "certificado"], ["almacenamiento"]),
    ("netapp-eseries", "NetApp E-Series (SANtricity)", "E2800, EF600", "httpapi", "netapp_eseries.santricity", 8443, ["clave"], ["almacenamiento"]),
    ("dell-powerstore", "Dell PowerStore", "PowerStore T / X", "httpapi", "dellemc.powerstore", 443, ["clave"], ["almacenamiento"]),
    ("dell-powermax", "Dell PowerMax (Unisphere)", "PowerMax 2500 / 8500", "httpapi", "dellemc.powermax", 8443, ["clave"], ["almacenamiento"]),
    ("dell-unity", "Dell Unity XT", "Unity XT 380 a 880", "httpapi", "dellemc.unity", 443, ["clave"], ["almacenamiento"]),
    ("dell-powerscale", "Dell PowerScale (Isilon)", "F-Series, H-Series", "httpapi", "dellemc.powerscale", 8080, ["clave"], ["almacenamiento"]),
    ("hpe-alletra", "HPE Alletra / Primera / 3PAR", "Alletra 9000, Primera, 3PAR", "httpapi", "hpe.alletra9000_primera_3par", 443, ["clave"], ["almacenamiento"]),
    ("hpe-nimble", "HPE Nimble / Alletra 6000", "Alletra 6000, Nimble AF/HF", "httpapi", "hpe.nimble", 5392, ["clave"], ["almacenamiento"]),
    ("pure-fa", "Pure Storage FlashArray", "FlashArray//X, //C, //XL", "httpapi", "purestorage.flasharray", 443, ["token"], ["almacenamiento"]),
    ("pure-fb", "Pure Storage FlashBlade", "FlashBlade//S, //E", "httpapi", "purestorage.flashblade", 443, ["token"], ["almacenamiento"]),
    ("ibm-flashsystem", "IBM FlashSystem / Storwize", "FlashSystem 5300 a 9500, SVC", "ssh", "ibm.storage_virtualize", 22, ["clave", "llave"], ["almacenamiento"]),
    ("ibm-ds8000", "IBM DS8000", "DS8900F", "httpapi", "ibm.ds8000", 8452, ["clave"], ["almacenamiento"]),
    ("hitachi-vsp", "Hitachi VSP", "VSP One Block, VSP E/G/F", "httpapi", "hitachivantara.vspone_block", 443, ["clave"], ["almacenamiento"]),
    ("huawei-oceanstor", "Huawei OceanStor / Dorado", "OceanStor Dorado", "httpapi", "community.general", 8088, ["clave"], ["almacenamiento"]),
    ("nutanix", "Nutanix (Files / Volumes, vía Prism)", "Prism Central", "httpapi", "nutanix.ncp", 9440, ["clave"], ["almacenamiento"]),
    ("ceph", "Red Hat Ceph Storage", "Clúster Ceph (cephadm / dashboard)", "ssh", "ceph.automation", 22, ["llave", "clave"], ["almacenamiento"]),
    ("odf", "OpenShift Data Foundation", "ODF sobre OpenShift", "kubernetes", "kubernetes.core", 6443, ["kubeconfig"], ["almacenamiento"]),
    ("minio", "MinIO / almacenamiento S3", "MinIO, Ceph RGW, S3 compatible", "httpapi", "amazon.aws", 9000, ["s3"], ["almacenamiento"]),
    ("truenas", "TrueNAS / NAS genérico (NFS, iSCSI, SMB)", "TrueNAS, NAS Linux", "ssh", "ansible.builtin", 22, ["llave", "clave", "token"], ["almacenamiento"]),
]

SERVIDORES = [
    ("linux-ssh", "Linux por SSH", "RHEL, CentOS, Rocky, Ubuntu, SUSE…", "ssh", "ansible.builtin", 22, ["llave", "clave"], ["servidor"]),
    ("windows-winrm", "Windows por WinRM", "Windows Server 2016 a 2025", "winrm", "ansible.windows", 5986, ["clave"], ["servidor"]),
    ("windows-ssh", "Windows por OpenSSH", "Windows Server 2019 o superior con OpenSSH", "ssh", "ansible.windows", 22, ["clave", "llave"], ["servidor"]),
    ("aix-ssh", "AIX / Solaris / HP-UX por SSH", "Unix tradicional", "ssh", "ibm.power_aix", 22, ["llave", "clave"], ["servidor"]),
]

ESTACIONES = [
    ("win-winrm", "Windows por WinRM", "Windows 10 / 11 Pro y Enterprise", "winrm", "ansible.windows", 5986, ["clave"], ["estación de trabajo"]),
    ("win-ssh", "Windows por OpenSSH", "Windows 10 / 11 con OpenSSH", "ssh", "ansible.windows", 22, ["clave", "llave"], ["estación de trabajo"]),
    ("linux-ssh", "Linux por SSH", "RHEL Workstation, Fedora, Ubuntu Desktop", "ssh", "ansible.builtin", 22, ["llave", "clave"], ["estación de trabajo"]),
    ("macos-ssh", "macOS por SSH", "macOS 13 o superior (Sesión remota activada)", "ssh", "community.general", 22, ["llave", "clave"], ["estación de trabajo"]),
]

CATALOGO = {"redes": RED, "storage": STORAGE, "inventario": SERVIDORES, "estaciones": ESTACIONES}
TIPOS = {"redes": [("router", "Router"), ("switch", "Switch"), ("firewall", "Firewall"), ("balanceador", "Balanceador")],
         "storage": [("almacenamiento", "Storage")], "inventario": [("servidor", "Servidor")],
         "estaciones": [("estación de trabajo", "Estación de trabajo")]}


def catalogo(seccion):
    """Para el formulario: tipos de equipo, marcas (con sus modelos, conexión y autenticaciones) y la etiqueta de cada autenticación."""
    return {"tipos": TIPOS.get(seccion, []),
            "marcas": [{"clave": c, "nombre": n, "modelos": m, "conexion": cx, "coleccion": col, "puerto": p, "auth": a, "tipos": t}
                       for c, n, m, cx, col, p, a, t in CATALOGO.get(seccion, [])],
            "auth": {k: v[0] for k, v in AUTENTICACION.items()}}


INVENTARIOS = {"redes": "FaceOps · Red (alta manual)", "storage": "FaceOps · Storage (alta manual)", "inventario": "FaceOps · Servidores (alta manual)",
               "estaciones": "FaceOps · Estaciones (alta manual)"}


CREDENCIAL = "FaceOps · Guardar credencial en la bóveda"


def agregar(usuario, cliente, seccion, f, prueba=False):
    """Valida el formulario, registra el host en AAP y el equipo en FaceOps y pide a AAP (ticket con aprobación) que guarde
    la credencial en la bóveda. Devuelve (id del equipo, ticket)."""
    from .aap import AAP
    from .boveda import disponible as bov_ok
    from .permisos import puede_ejecutar
    from .pruebas import estado as estado_prueba, exceptuada
    marca = next((x for x in CATALOGO.get(seccion, []) if x[0] == f.get("marca")), None)
    if not marca:
        raise ValueError("Elige la marca o plataforma")
    clave, nombre_m, _, conexion, coleccion, puerto_def, auths, tipos = marca
    tipo = f.get("tipo") or tipos[0]
    if tipo not in tipos:
        raise ValueError("Ese tipo de equipo no corresponde a la marca elegida")
    nombre = (f.get("nombre") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,62}", nombre):
        raise ValueError("Nombre: minúsculas, números, puntos o guiones (ej. fw-borde-01)")
    ip = (f.get("ip") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.:-]{3,253}", ip):
        raise ValueError("Escribe la IP o el nombre DNS del equipo")
    existente = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], nombre)
    if existente and f.get("vincular") != "1":
        raise ValueError(f"Ya existe un equipo llamado {nombre}")
    auth = f.get("auth") or auths[0]
    if auth not in auths:
        raise ValueError("Ese método de autenticación no aplica a esta marca")
    secreto = (f.get("secreto") or "").strip()
    usuario_eq = (f.get("usuario") or "").strip()
    if not secreto:
        raise ValueError(f"Falta: {AUTENTICACION[auth][0].lower()}")
    if auth in ("clave", "llave", "s3") and not usuario_eq:
        raise ValueError("Falta el usuario" if auth != "s3" else "Falta la access key")
    try:
        puerto = int(f.get("puerto") or puerto_def)
    except ValueError:
        raise ValueError("Puerto inválido") from None
    if not bov_ok():
        raise ValueError("No hay bóveda conectada: el secreto no se puede guardar de forma segura")
    a_cred = q1("SELECT * FROM automatizaciones WHERE cliente_id=? AND nombre_aap=?", cliente["id"], CREDENCIAL)
    if not a_cred:
        raise ValueError(f"Falta «{CREDENCIAL}» en el catálogo (sincroniza con AAP)")
    ok_, por_que = puede_ejecutar(usuario, cliente["id"], a_cred)
    if not ok_:
        raise PermissionError(por_que)
    if not prueba and estado_prueba(dict(a_cred, cliente_id=cliente["id"]))["estado"] != "certificada" and not exceptuada(CREDENCIAL):
        raise ValueError(f"«{a_cred['nombre']}» está bloqueada hasta pasar su plan de prueba: el alta manual no se puede completar todavía")
    protocolo = "rdp" if clave.startswith(("windows", "win-")) and conexion == "winrm" else ("ssh" if conexion in ("ssh", "network_cli") else conexion)
    params = {"cb_maquina": nombre, "cb_metodo": auth, "cb_usuario": usuario_eq, "cb_protocolo": protocolo, "cb_puerto": puerto, "cb_secreto": "••••••"}
    if existente:   # vincular un equipo que FaceOps ya conoce: basta su credencial en la bóveda (la guarda AAP)
        t = _pedir_credencial(usuario, cliente, a_cred, existente, params, secreto, prueba)
        auditar(usuario["usuario"], cliente["id"], "vincular equipo a la bóveda (a mano)", nombre,
                {"metodo": auth, "protocolo": protocolo, "puerto": puerto, "ticket": t["numero"]})
        return existente["id"], t
    # host en AAP con sus variables de conexión (sin secretos)
    hv = {"ansible_host": ip, "ansible_port": puerto, "faceops_categoria": tipo, "faceops_fabricante": nombre_m,
          "faceops_modelo": (f.get("modelo") or "").strip(), "faceops_coleccion": coleccion, "faceops_alta": "manual",
          "faceops_tls_verificar": f.get("tls") == "si"}
    if conexion in ("network_cli", "netconf", "httpapi") and clave in NETWORK_OS:
        hv.update({"ansible_connection": f"ansible.netcommon.{conexion}", "ansible_network_os": NETWORK_OS[clave]})
    elif conexion == "winrm":
        hv.update({"ansible_connection": "winrm", "ansible_winrm_transport": f.get("transporte") or "ntlm",
                   "ansible_winrm_server_cert_validation": "validate" if f.get("tls") == "si" else "ignore"})
    elif conexion in ("httpapi", "kubernetes"):
        hv.update({"ansible_connection": "local"})   # API REST: los módulos llaman al equipo desde el entorno de ejecución
    aap = AAP.de_cliente(cliente)
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    inv_n = INVENTARIOS[seccion]
    inv = (aap.get("inventories/", name=inv_n).get("results") or [None])[0] or aap.post("inventories/", {
        "name": inv_n, "organization": org, "description": "Equipos que ya existían, incorporados a mano desde FaceOps"})
    grupo = {"router": "routers", "switch": "switches", "firewall": "firewalls", "balanceador": "balanceadores",
             "almacenamiento": "storage", "servidor": "windows" if clave.startswith("windows") else "linux", "estación de trabajo": "estaciones"}[tipo]
    grp = (aap.get("groups/", name=grupo, inventory=inv["id"]).get("results") or [None])[0] or aap.post("groups/", {"name": grupo, "inventory": inv["id"]})
    host = aap.post("hosts/", {"name": nombre, "inventory": inv["id"], "variables": json.dumps(hv, ensure_ascii=False)})
    aap.post(f"groups/{grp['id']}/hosts/", {"id": host["id"]})
    datos = {"alta_manual": {"marca": nombre_m, "modelo": hv["faceops_modelo"], "conexion": conexion, "coleccion": coleccion,
                             "autenticacion": AUTENTICACION[auth][0], "puerto": puerto, "por": usuario["usuario"], "cuando": ahora()}}
    valores = (host["id"], inv_n, f"{nombre_m} {hv['faceops_modelo']}".strip(), ip, json.dumps(datos, ensure_ascii=False),
               (f.get("ubicacion") or "").strip(), (f.get("ambiente") or "").strip(), "inventariado", ahora(), tipo,
               "virtual" if f.get("forma") == "virtual" else "física")
    retirado = q1("SELECT id FROM equipos WHERE cliente_id=? AND nombre=? AND estado='retirado'", cliente["id"], nombre)
    if retirado:   # el nombre ya existió y se retiró: vuelve el mismo registro (el nombre es único por cliente)
        ej("UPDATE equipos SET aap_host_id=?, inventario=?, so=?, ip=?, datos=?, ubicacion=?, ambiente=?, estado=?, visto=?, categoria=?, forma=? "
           "WHERE id=?", *valores, retirado["id"])
    else:
        ej("INSERT INTO equipos(cliente_id, nombre, aap_host_id, inventario, so, ip, datos, ubicacion, ambiente, estado, visto, categoria, forma) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", cliente["id"], nombre, *valores)
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], nombre)
    t = _pedir_credencial(usuario, cliente, a_cred, e, params, secreto, prueba)
    auditar(usuario["usuario"], cliente["id"], "agregar equipo a mano", nombre,
            {k: v for k, v in datos["alta_manual"].items()} | {"ip": ip, "seccion": seccion, "ticket": t["numero"]})
    return e["id"], t


def _pedir_credencial(usuario, cliente, a_cred, e, params, secreto, prueba=False):
    """Ticket de «Guardar credencial en la bóveda» sobre el equipo: el secreto solo viaja a AAP (enmascarado en FaceOps)."""
    from .operacion import solicitar
    return solicitar(usuario, cliente, a_cred, None, params, f"Alta manual: credencial de {e['nombre']} en la bóveda",
                     {"cb_secreto": secreto}, {"ambientes": [e["ambiente"]]} if e.get("ambiente") else {}, equipo=dict(e), prueba=prueba)


def credencial_guardada(cliente, t):
    """Tras el ticket exitoso: el equipo queda con su vínculo a la bóveda (FaceOps solo lee la credencial)."""
    nombre = (t["limite"] or "").split(":")[0]
    e = q1("SELECT * FROM equipos WHERE cliente_id=? AND nombre=? AND estado!='retirado'", cliente["id"], nombre)
    if not e:
        return f"{nombre}: no está en el inventario"
    d = json.loads(e["datos"] or "{}")
    p = json.loads(t["parametros"] or "{}")
    d["vinculo"] = {"ok": True, "protocolo": p.get("cb_protocolo", ""), "metodo": p.get("cb_metodo", ""), "ticket": t["numero"], "cuando": ahora()}
    ej("UPDATE equipos SET datos=? WHERE id=?", json.dumps(d, ensure_ascii=False), e["id"])
    try:
        from .boveda import _lista
        _lista.update(t=0, v=None)   # la lista de máquinas con credencial se relee
    except Exception:  # noqa: BLE001
        pass
    return f"{nombre}: credencial en la bóveda (la guardó AAP)"
