"""Infraestructuras del cliente: dónde viven sus equipos (centro de datos on-premise, VMware, GCP, AWS, Azure…) y cómo
llega FaceOps a cada una. Al incorporar una, sus secretos van directo a una credencial cifrada de AAP (FaceOps no los
guarda), se crea su inventario en AAP (con fuente dinámica en las nubes) y sus equipos entran al inventario de FaceOps."""
import json
import re
import shlex

from .bd import ahora, auditar, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS infraestructuras (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, tipo TEXT NOT NULL, region TEXT DEFAULT '',
  detalle TEXT DEFAULT '{}', credencial TEXT DEFAULT '', inventario TEXT DEFAULT '', ubicacion TEXT DEFAULT '',
  estado TEXT DEFAULT 'sin probar', verificado TEXT, mensaje TEXT DEFAULT '', creado TEXT, centro_datos TEXT DEFAULT '',
  UNIQUE(cliente_id, nombre));
"""

# proveedor → nombre, ámbito, campos (var, etiqueta, secreto, tipo de control), tipo de credencial de AAP y fuente de inventario
OPCIONALES = {"srv_bmc", "modelo", "equipos", "fabricante", "consola", "version", "proyectos", "red", "vpc", "datacenter", "bmc_tipo", "bmc_usuario", "bmc_clave", "sala", "carpeta", "tls", "acc_usuario", "acc_llave", "acc_clave"}
# Acceso a las máquinas (nubes y vSphere): una credencial para todas sus máquinas. Si se deja vacío, la automatización
# «FaceOps · Incorporar máquinas» deja en cada una el usuario de servicio de FaceOps con su propia llave.
ACCESO = [("acc_usuario", "Usuario en las máquinas (ej. ansible, ec2-user, Administrator)", False, "text"),
          ("acc_llave", "Llave privada SSH (Linux)", True, "area"), ("acc_clave", "Clave (Windows / WinRM)", True, "password")]
PROVEEDORES = {
    "gcp": {"nombre": "Google Cloud", "ambito": "nube", "fuente": "gce", "credencial_aap": "Google Compute Engine",
            "ayuda": "Cuenta de servicio del proyecto con su llave JSON: rol Compute Viewer para leer; Compute Admin si FaceOps también creará o migrará máquinas",
            "guia": ("Todo de una vez en Cloud Shell: «gcloud iam service-accounts create faceops-lectura» · «gcloud projects add-iam-policy-binding &lt;PROYECTO&gt; "
                     "--member=serviceAccount:faceops-lectura@&lt;PROYECTO&gt;.iam.gserviceaccount.com --role=roles/compute.viewer» · "
                     "«gcloud iam service-accounts keys create faceops.json --iam-account=faceops-lectura@&lt;PROYECTO&gt;.iam.gserviceaccount.com»; pega el contenido de faceops.json.",
                     "https://cloud.google.com/iam/docs/keys-create-delete"),
            "ayudas": {
                "proyecto": ("master-ia-320218", "ID del proyecto (no el nombre): selector de proyectos en la consola · CLI: gcloud config get-value project",
                             "https://console.cloud.google.com/home/dashboard"),
                "zona": ("southamerica-west1-a", "Zona donde están las máquinas · CLI: gcloud compute zones list",
                         "https://cloud.google.com/compute/docs/regions-zones"),
                "cuenta": ("{ \"type\": \"service_account\", … }", "IAM › Cuentas de servicio › Crear (faceops-lectura, rol Compute Viewer) › Claves › Agregar clave › JSON; pega el archivo completo",
                           "https://console.cloud.google.com/iam-admin/serviceaccounts/create"),
                "red": ("default", "Nombre de la red VPC (opcional) · Red de VPC › Redes de VPC",
                        "https://console.cloud.google.com/networking/networks/list")},
            "campos": [("proyecto", "ID del proyecto", False, "text"), ("zona", "Zona (ej. southamerica-west1-a)", False, "text"),
                       ("cuenta", "Cuenta de servicio (JSON completo)", True, "area"), ("red", "Red VPC", False, "text")]},
    "aws": {"nombre": "Amazon Web Services", "ambito": "nube", "fuente": "ec2", "credencial_aap": "Amazon Web Services",
            "ayuda": "Usuario IAM con acceso programático (EC2, VPC, Route 53 si se instala OpenShift)",
            "ayudas": {
                "usuario_iam": ("faceops-lectura", "Nombre del usuario IAM dueño de la clave (opcional): con él, el enlace de la clave abre directo sus credenciales",
                                "https://console.aws.amazon.com/iam/home#/users"),
                "access_key": ("AKIA…", "IAM › Usuarios › tu usuario › Credenciales de seguridad › Crear clave de acceso",
                               "https://console.aws.amazon.com/iam/home#/users/create"),
                "secret_key": ("~40 caracteres", "Se muestra una sola vez al crear la clave de acceso (mismo paso que el Access key ID)",
                               "https://console.aws.amazon.com/iam/home#/users/create"),
                "region": ("sa-east-1", "Código de la región · CLI: aws configure get region",
                           "https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html"),
                "vpc": ("vpc-0123456789abcdef0", "ID de la VPC (opcional) · VPC › Sus VPC",
                        "https://console.aws.amazon.com/vpcconsole/home#vpcs:")},
            "campos": [("usuario_iam", "Usuario IAM (opcional)", False, "text"), ("access_key", "Access key ID", False, "text"), ("secret_key", "Secret access key", True, "password"),
                       ("region", "Región (ej. sa-east-1)", False, "text"), ("vpc", "VPC", False, "text")]},
    "azure": {"nombre": "Microsoft Azure", "ambito": "nube", "fuente": "azure_rm", "credencial_aap": "Microsoft Azure Resource Manager",
              "ayuda": "Service principal (App registration) con rol Contributor en la suscripción",
              "guia": ("Todo de una vez: en Azure Cloud Shell ejecuta «az ad sp create-for-rbac --name faceops --role Contributor --scopes /subscriptions/&lt;ID de suscripción&gt;»; "
                       "entrega appId (Client ID), password (Client secret) y tenant (Tenant ID).", "https://learn.microsoft.com/cli/azure/azure-cli-sp-tutorial-1"),
              "ayudas": {   # formato del dato y dónde se obtiene (portal o CLI)
                  "suscripcion": ("00000000-0000-0000-0000-000000000000", "Portal › Suscripciones › columna «Id. de suscripción» · CLI: az account show --query id",
                                  "https://portal.azure.com/#view/Microsoft_Azure_Billing/SubscriptionsBlade"),
                  "tenant": ("00000000-0000-0000-0000-000000000000", "Microsoft Entra ID › Información general › «Id. de inquilino» · CLI: az account show --query tenantId",
                             "https://portal.azure.com/#view/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade/~/Overview"),
                  "cliente": ("00000000-0000-0000-0000-000000000000", "Microsoft Entra ID › Registros de aplicaciones › tu aplicación › «Id. de aplicación (cliente)»",
                              "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"),
                  "secreto": ("~40 caracteres", "Misma aplicación › Certificados y secretos › Nuevo secreto de cliente · copia el «Valor» (se muestra una sola vez)",
                              "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"),
                  "region": ("brazilsouth", "Nombre corto de la región · CLI: az account list-locations -o table",
                             "https://learn.microsoft.com/azure/reliability/regions-list")},
              "campos": [("suscripcion", "ID de suscripción", False, "text"), ("tenant", "Tenant ID", False, "text"),
                         ("cliente", "Client ID (App ID)", False, "text"), ("secreto", "Client secret", True, "password"),
                         ("region", "Región (ej. brazilsouth)", False, "text")]},
    "vmware": {"nombre": "VMware vSphere", "ambito": "on-premise", "fuente": "vmware", "credencial_aap": "VMware vCenter",
               "ayuda": "Usuario de vCenter con permisos sobre el datacenter (lectura para inventario; administrador de VM para crear)",
               "campos": [("host", "vCenter (FQDN o IP, con :puerto si no es 443)", False, "text"), ("usuario", "Usuario (ej. faceops@vsphere.local)", False, "text"),
                          ("clave", "Clave", True, "password"), ("datacenter", "Datacenter", False, "text"), ("carpeta", "Carpeta de VM", False, "text"),
                          ("tls", "Validar el certificado del vCenter (si / no)", False, "text")]},
    "kvm": {"nombre": "Hipervisor KVM", "ambito": "on-premise", "fuente": None, "credencial_aap": "Machine",
            "ayuda": "Anfitrión con libvirt; el usuario necesita sudo para virsh y virt-install",
            "campos": [("anfitrion", "Anfitrión KVM (IP o nombre)", False, "text"), ("usuario", "Usuario SSH", False, "text"),
                       ("llave", "Llave privada SSH", True, "area"), ("red", "Red de las VM (ej. 192.168.122.0/24)", False, "text")]},
    "openshift": {"nombre": "Red Hat OpenShift", "ambito": "on-premise", "fuente": None, "credencial_aap": "OpenShift or Kubernetes API Bearer Token",
                  "ayuda": "Clúster OpenShift (contenedores): API y token de una cuenta de servicio con permisos sobre los proyectos que FaceOps administra",
                  "campos": [("api", "API del clúster (ej. https://api.cluster.dominio:6443)", False, "text"), ("token", "Token de la cuenta de servicio", True, "password"),
                             ("consola", "Consola web", False, "text"), ("version", "Versión", False, "text")]},
    "openshift-virt": {"nombre": "OpenShift Virtualization", "ambito": "on-premise", "fuente": None, "credencial_aap": "OpenShift or Kubernetes API Bearer Token",
                       "ayuda": "Máquinas virtuales sobre OpenShift (KubeVirt): API y token con permisos sobre los proyectos de las VM",
                       "campos": [("api", "API del clúster (ej. https://api.cluster.dominio:6443)", False, "text"), ("token", "Token de la cuenta de servicio", True, "password"),
                                  ("consola", "Consola web", False, "text"), ("proyectos", "Proyectos con VM (separados por coma)", False, "text")]},
    "fisico": {"nombre": "Servidores físicos", "ambito": "on-premise", "fuente": None, "credencial_aap": "Machine",
               "ayuda": "Servidores bare metal: acceso SSH al sistema y, si tienen, gestión fuera de banda (iDRAC, iLO, XClarity) por Redfish o IPMI",
               "campos": [("fabricante", "Fabricante", False, "fabricante"), ("modelo", "Modelo", False, "modelo"),
                          ("srv_nombre", "Nombre del servidor", False, "text"), ("srv_ip", "IP del sistema", False, "text"),
                          ("srv_bmc", "IP de gestión (BMC)", False, "text"), ("sala", "Sala / rack", False, "text"),
                          ("usuario", "Usuario SSH", False, "text"), ("llave", "Llave privada SSH", True, "area"),
                          ("bmc_tipo", "Gestión fuera de banda", False, "bmc"), ("bmc_usuario", "Usuario BMC", False, "text"),
                          ("bmc_clave", "Clave BMC", True, "password")]},
}
# Las nubes (GCP, AWS, Azure) se conectan solo contra la API del proveedor: no piden acceso a las máquinas. Si una nube
# antigua guardó ese acceso, sus datos se conservan tal cual (no se muestran ni se tocan al editar).
for _k in ("vmware",):
    PROVEEDORES[_k]["campos"] = PROVEEDORES[_k]["campos"] + ACCESO
    PROVEEDORES[_k]["acceso"] = True
# Campos obligatorios por proveedor (asterisco rojo y «required» en los formularios). Los que no figuran aquí siguen la
# regla anterior: obligatorio todo campo que no esté en OPCIONALES.
OBLIGATORIOS = {"azure": {"suscripcion", "tenant", "cliente", "secreto", "region"},
                "gcp": {"proyecto", "zona", "cuenta"},
                "aws": {"access_key", "secret_key", "region"},
                "vmware": {"host", "usuario", "clave", "datacenter"}}
for _k, _p in PROVEEDORES.items():
    _p["obligatorios"] = OBLIGATORIOS.get(_k) or {c for c, _, _, _ in _p["campos"] if c not in OPCIONALES}
ACCESO_CAMPOS = {c for c, _, _, _ in ACCESO}
ENCRIPTADO = "$encrypted$"   # en un PATCH de AAP, conserva el secreto que la credencial ya tiene
# Servidores físicos: fabricante → (gestión fuera de banda, modelos). Se agregan de a uno; cada fabricante es un grupo.
FABRICANTES = {
    "Dell": ("iDRAC · Redfish", ["PowerEdge R650", "PowerEdge R650xs", "PowerEdge R660", "PowerEdge R660xs", "PowerEdge R750", "PowerEdge R750xs",
                                 "PowerEdge R760", "PowerEdge R760xs", "PowerEdge R6625", "PowerEdge R7625", "PowerEdge R7615", "PowerEdge R840", "PowerEdge R940",
                                 "PowerEdge MX750c", "PowerEdge XE9680"]),
    "HPE": ("iLO · Redfish", ["ProLiant DL360 Gen10 Plus", "ProLiant DL380 Gen10 Plus", "ProLiant DL360 Gen11", "ProLiant DL380 Gen11",
                              "ProLiant DL385 Gen11", "ProLiant DL560 Gen11", "Synergy 480 Gen11", "Alletra 4110"]),
    "Lenovo": ("XClarity Controller · Redfish", ["ThinkSystem SR630 V2", "ThinkSystem SR650 V2", "ThinkSystem SR630 V3", "ThinkSystem SR650 V3",
                                                  "ThinkSystem SR665 V3", "ThinkSystem SR850 V3", "ThinkSystem SR950 V3", "ThinkAgile HX650 V3"]),
    "Cisco": ("CIMC · Redfish", ["UCS C220 M6", "UCS C240 M6", "UCS C220 M7", "UCS C240 M7", "UCS X210c M7", "UCS B200 M6"]),
    "Supermicro": ("BMC · Redfish / IPMI", ["SuperServer SYS-120U", "SuperServer SYS-220U", "SuperServer AS-1125HS", "SuperServer AS-2125HS", "Hyper SYS-621H"]),
    "Huawei / xFusion": ("iBMC · Redfish", ["FusionServer 1288H V7", "FusionServer 2288H V7", "FusionServer 2488H V7"]),
    "Fujitsu": ("iRMC · Redfish", ["PRIMERGY RX2530 M7", "PRIMERGY RX2540 M7", "PRIMERGY RX4770 M7"]),
    "Inspur": ("BMC · Redfish", ["NF5180M6", "NF5280M6", "NF5280M7"]),
    "IBM": ("BMC · Redfish", ["Power S1022", "Power S1024", "Power E1050"]),
    "Otro": ("Redfish / IPMI", ["Otro modelo"]),
}
AMBITOS = {"on-premise": "On-premise", "nube": "Nube"}
# Qué tipo de cómputo entrega cada infraestructura (un centro de datos puede tener varias)
NATURALEZAS = {"vm": "Máquinas virtuales", "contenedores": "Contenedores", "fisicas": "Máquinas físicas", "nube": "Nube pública"}
NATURALEZA = {"vmware": "vm", "kvm": "vm", "openshift-virt": "vm", "openshift": "contenedores", "fisico": "fisicas",
              "gcp": "nube", "aws": "nube", "azure": "nube"}
for _k, _p in PROVEEDORES.items():
    _p["naturaleza"] = NATURALEZA.get(_k, "vm")
NUBE_PUBLICA = "Nube pública"   # (anterior) hoy cada nube es su propio centro de datos: GCP, AWS, Azure
CENTRO_NUBE = {"gcp": "Cloud GCP", "aws": "AWS", "azure": "Azure"}


def etiqueta_centro(infras_centro):
    """Cada centro de datos se etiqueta «cloud» (todas sus infraestructuras son de nube) u «onpremise»."""
    return "cloud" if infras_centro and all(ambito(x["tipo"]) == "nube" for x in infras_centro) else "onpremise"


def ambito(tipo):
    return PROVEEDORES.get(tipo, {}).get("ambito", "on-premise")


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)
    if "centro_datos" not in {r[1] for r in con().execute("PRAGMA table_info(infraestructuras)")}:
        con().execute("ALTER TABLE infraestructuras ADD COLUMN centro_datos TEXT DEFAULT ''")
        con().commit()


def sembrar(cliente_id):
    """Las infraestructuras que ya existen en el stand, agrupadas por centro de datos."""
    # antes la infraestructura KVM se llamaba como su centro de datos: ahora el centro de datos agrupa y la infra es «KVM · Santiago»
    if q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre='Centro de datos Santiago'", cliente_id) and \
            not q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre='KVM · Santiago'", cliente_id):
        ej("UPDATE infraestructuras SET nombre='KVM · Santiago' WHERE cliente_id=? AND nombre='Centro de datos Santiago'", cliente_id)
    for n, t, reg, det, cred, inv, ubi, cd, est in (
            ("KVM · Santiago", "kvm", "Santiago, Chile",
             {"anfitrion": "192.168.122.1", "usuario": "aap-kvm", "red": "192.168.122.0/24", "hipervisor": "KVM / libvirt"},
             "FaceOps · anfitrión KVM (ssh)", "FaceOps · Infraestructura", "Centro de datos Santiago", "Centro de datos Santiago", "conectada"),
            ("OpenShift · SNO", "openshift", "Santiago, Chile",
             {"api": "https://192.168.122.50:6443", "consola": "https://console.sno.34.176.42.56.sslip.io", "version": "4.19.51",
              "topología": "un nodo (SNO) sobre KVM", "cluster": "sno"},
             "", "", "OpenShift · SNO", "Centro de datos Santiago", "sin credencial"),
            ("Cloud GCP · invoke-summit-cloud", "gcp", "southamerica-west1 (Santiago)",
             {"proyecto": "invoke-summit-cloud", "zona": "southamerica-west1-a", "red": "default", "etiqueta": "demo=summit-cloud"},
             "GCP · invoke-summit-cloud", "Nube GCP", "Cloud GCP", "Cloud GCP", "sin probar"),
            ("KVM · Valparaíso", "kvm", "Valparaíso, Chile",
             {"anfitrion": "192.168.122.1", "usuario": "aap-kvm", "red": "192.168.150.0/24", "red_libvirt": "dc-valpo",
              "pool": "/var/lib/libvirt/dc-valpo", "hipervisor": "KVM / libvirt", "emulado": "sí: comparte el anfitrión con Santiago (red y pool propios)"},
             "FaceOps · anfitrión KVM (ssh)", "FaceOps · Infraestructura", "Centro de datos Valparaíso", "Centro de datos Valparaíso", "conectada")):
        ej("UPDATE infraestructuras SET centro_datos='Cloud GCP' WHERE cliente_id=? AND tipo='gcp' AND centro_datos='GCP'", cliente_id)
        # renombrada con «Editar configuración»: se reconoce por su tipo y ubicación (que no cambian) y no se vuelve a sembrar
        if not q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND (nombre=? OR (tipo=? AND ubicacion=?))", cliente_id, n, t, ubi):
            ej("INSERT INTO infraestructuras(cliente_id, nombre, tipo, region, detalle, credencial, inventario, ubicacion, estado, creado, centro_datos) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?)", cliente_id, n, t, reg, json.dumps(det, ensure_ascii=False), cred, inv, ubi, est, ahora(), cd)
        else:
            ej("UPDATE infraestructuras SET centro_datos=? WHERE cliente_id=? AND (nombre=? OR (tipo=? AND ubicacion=?)) AND COALESCE(centro_datos,'')=''", cd, cliente_id, n, t, ubi)


def lista(cliente_id, eqs=None):
    out = q("SELECT * FROM infraestructuras WHERE cliente_id=? ORDER BY id", cliente_id)
    for x in out:
        x["detalle_d"] = j(x["detalle"], {})
        x["prov"] = PROVEEDORES.get(x["tipo"], {"nombre": x["tipo"]})
        x["ambito"] = ambito(x["tipo"])
        x["naturaleza"] = NATURALEZA.get(x["tipo"], "vm")
        cd_ = x.get("centro_datos") or ""
        if x["ambito"] == "nube" and (not cd_ or cd_ == NUBE_PUBLICA):   # cada nube es su propio centro de datos (cloud)
            cd_ = CENTRO_NUBE.get(x["tipo"], x["prov"]["nombre"])
        x["centro"] = cd_ or x["ubicacion"] or "Sin centro de datos"
        x["fabricante"] = (x["detalle_d"].get("fabricante") or "").strip() if x["tipo"] == "fisico" else ""   # físicas: agrupadas por fabricante
        if x["tipo"] in ("openshift", "openshift-virt") and x["detalle_d"].get("cluster"):
            x["equipos"] = len([e for e in (eqs or []) if e.get("nombre") == "ocp-" + x["detalle_d"]["cluster"]])
            x["unidad"] = "clúster"
        else:
            x["equipos"] = len([e for e in (eqs or []) if (e.get("ubicacion") or "") == x["ubicacion"]])
            x["unidad"] = "equipo"
    return out


def tickets_de(cliente_id, nombre, limite=100):
    """Tickets de una infraestructura (nube, hipervisor), los más recientes primero: los que llevan su nombre como valor de
    un parámetro (nube, nv_nube, hv_infra…). Para la lista al pie de su ficha."""
    if not nombre:
        return []
    return q("SELECT t.id, t.numero, t.estado, t.creado, t.fin, t.motivo, a.nombre accion, u.nombre solicitante FROM tickets t "
             "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id LEFT JOIN usuarios u ON u.id=t.usuario_id "
             "WHERE t.cliente_id=? AND instr(COALESCE(t.parametros,''), ?) > 0 ORDER BY t.creado DESC, t.id DESC LIMIT ?",
             cliente_id, json.dumps(nombre, ensure_ascii=False), limite)


def _valores(p, f, exigir=True):
    v = {c: (f.get(c) or "").strip() for c, _, _, _ in p["campos"]}
    faltan = [et for c, et, sec, _ in p["campos"] if not v[c] and c in p["obligatorios"]]
    if exigir and faltan:
        raise ValueError("Faltan: " + ", ".join(faltan))
    return v


def _equipos_fisicos(texto):
    out = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if not linea.strip() or linea.strip().startswith("#"):
            continue
        partes = [x.strip() for x in linea.replace(";", ",").split(",")]
        if len(partes) < 2 or not partes[0] or not partes[1]:
            raise ValueError(f"Equipos, línea {n}: use «nombre, IP del sistema[, IP de gestión BMC]»")
        out.append({"nombre": partes[0], "ip": partes[1], "bmc": partes[2] if len(partes) > 2 else ""})
    if not out:
        raise ValueError("Agregue al menos un equipo")
    return out


def _vc(host):
    """«vcenter.dominio:8989» → ("vcenter.dominio", 8989); sin puerto, el 443 estándar."""
    h, _, pto = (host or "").strip().partition(":")
    return h, int(pto) if pto.isdigit() else 443


def _credencial(aap, org, tipo, nombre, v, existente="", previo=None):
    """Crea (o actualiza) la credencial cifrada de AAP con los secretos del formulario. Devuelve su nombre.
    Un secreto con valor ENCRIPTADO conserva el que la credencial ya tiene (previo = sus inputs actuales)."""
    p = PROVEEDORES[tipo]
    ct = (aap.get("credential_types/", name=p["credencial_aap"]).get("results") or [None])[0]
    if not ct:
        raise ValueError(f"AAP no tiene el tipo de credencial «{p['credencial_aap']}»")
    llave_ = lambda s: s if s == ENCRIPTADO else s.strip() + "\n"
    if tipo in ("kvm", "fisico"):
        ins = {"username": v["usuario"], "ssh_key_data": llave_(v["llave"]), "become_method": "sudo"}
    elif tipo in ("openshift", "openshift-virt"):
        ins = {"host": v["api"], "bearer_token": v["token"], "verify_ssl": False}
    elif tipo == "vmware":
        ins = {"host": _vc(v["host"])[0], "username": v["usuario"], "password": v["clave"]}
    elif tipo == "gcp" and v["cuenta"] == ENCRIPTADO:   # misma cuenta de servicio: solo cambia el proyecto
        ins = {"username": (previo or {}).get("username", ""), "project": v["proyecto"] or (previo or {}).get("project", ""), "ssh_key_data": ENCRIPTADO}
    elif tipo == "gcp":
        try:
            sa = json.loads(v["cuenta"])
            assert sa.get("client_email") and sa.get("private_key")
        except (ValueError, AssertionError):
            raise ValueError("La cuenta de servicio debe ser el JSON que entrega GCP (con client_email y private_key)")
        ins = {"username": sa["client_email"], "project": v["proyecto"] or sa.get("project_id", ""), "ssh_key_data": sa["private_key"]}
    elif tipo == "aws":
        ins = {"username": v["access_key"], "password": v["secret_key"]}
    else:
        ins = {"subscription": v["suscripcion"], "tenant": v["tenant"], "client": v["cliente"], "secret": v["secreto"]}
    nom = existente or f"Infra · {nombre}"
    datos = {"name": nom, "organization": org, "credential_type": ct["id"], "inputs": ins, "description": f"Acceso de FaceOps a {nombre} ({p['nombre']})"}
    ya = (aap.get("credentials/", name=nom).get("results") or [None])[0]
    if ya:
        aap.patch(f"credentials/{ya['id']}/", datos)
        return ya
    return aap.post("credentials/", datos)


def _credencial_bmc(aap, org, nombre, v):
    if not v.get("bmc_usuario") or not v.get("bmc_clave"):
        return None
    ct = (aap.get("credential_types/", name="Infra · BMC (Redfish / IPMI)").get("results") or [None])[0] or aap.post("credential_types/", {
        "name": "Infra · BMC (Redfish / IPMI)", "kind": "cloud", "description": "Gestión fuera de banda de servidores físicos (iDRAC, iLO, XClarity)",
        "inputs": {"fields": [{"id": "usuario", "label": "Usuario", "type": "string"}, {"id": "clave", "label": "Clave", "type": "string", "secret": True}],
                   "required": ["usuario", "clave"]},
        "injectors": {"extra_vars": {"bmc_usuario": "{{ usuario }}", "bmc_clave": "{{ clave }}"}}})
    nom = f"Infra · {nombre} · BMC"
    datos = {"name": nom, "organization": org, "credential_type": ct["id"], "inputs": {"usuario": v["bmc_usuario"], "clave": v["bmc_clave"]}}
    ya = (aap.get("credentials/", name=nom).get("results") or [None])[0]
    return aap.patch(f"credentials/{ya['id']}/", datos) if ya else aap.post("credentials/", datos)


def _credencial_maquinas(aap, org, nombre, usuario, llave="", clave=""):
    """Credencial «Machine» con la que FaceOps entra a las máquinas de una infraestructura (todas, no una por una)."""
    if not usuario or not (llave or clave):
        return None
    ct = (aap.get("credential_types/", name="Machine", managed=True).get("results") or [None])[0]
    ins = {"username": usuario, "become_method": "sudo"}
    if llave:
        ins["ssh_key_data"] = llave.strip() + "\n"
    if clave:
        ins["password"] = clave
    nom = f"Infra · {nombre} · máquinas"
    datos = {"name": nom, "organization": org, "credential_type": ct["id"], "inputs": ins,
             "description": f"Acceso de FaceOps a las máquinas de {nombre}"}
    ya = (aap.get("credentials/", name=nom).get("results") or [None])[0]
    return aap.patch(f"credentials/{ya['id']}/", datos) if ya else aap.post("credentials/", datos)


def plan_acceso(cliente_id, inventario, credencial):
    """El inventario de la infraestructura entra al plan de recolección con su credencial: así FaceOps llega a cada
    máquina importada (recolector, skills, políticas, CVE) sin pedir credenciales caso a caso."""
    c = q1("SELECT recoleccion FROM clientes WHERE id=?", cliente_id)
    plan = j(c["recoleccion"], {})
    plan[inventario] = [{"limite": "", "credencial": credencial}]
    ej("UPDATE clientes SET recoleccion=? WHERE id=?", json.dumps(plan, ensure_ascii=False), cliente_id)


def _fuente(aap, inv, nombre, tipo, cred, v):
    p = PROVEEDORES[tipo]
    if not p["fuente"]:
        return
    sv = {"gce": {"zones": [v.get("zona")]} if v.get("zona") else {}, "ec2": {"regions": [v.get("region")]} if v.get("region") else {},
          "azure_rm": {}, "vmware": {}}[p["fuente"]]
    if p["fuente"] == "gce":   # las VM Windows se alcanzan por WinRM HTTPS (5986) con el usuario de servicio; las Linux por SSH
        win = "(disks | map(attribute='licenses', default=[]) | flatten | select('search', 'windows') | list | length) > 0"
        sv["compose"] = {"ansible_connection": f"'winrm' if {win} else 'ssh'", "ansible_port": f"5986 if {win} else 22",
                         "ansible_winrm_transport": "'ntlm'", "ansible_winrm_scheme": "'https'",
                         "ansible_winrm_server_cert_validation": "'ignore'"}
    if p["fuente"] == "vmware":
        sv = {"port": _vc(v.get("host", ""))[1], "validate_certs": (v.get("tls") or "si").lower() not in ("no", "false", "0"),
              "hostnames": ["config.name"], "with_tags": False,
              "properties": ["name", "config.name", "config.uuid", "config.instanceUuid", "config.guestId", "config.guestFullName", "config.template",
                             "config.hardware.numCPU", "config.hardware.memoryMB", "guest.ipAddress", "guest.hostName", "guest.guestState",
                             "summary.runtime.powerState", "runtime.connectionState"]}
    nom = f"{nombre} · {p['nombre']}"
    datos = {"name": nom, "inventory": inv["id"], "source": p["fuente"], "credential": cred["id"], "overwrite": True,
             "update_on_launch": False, "source_vars": json.dumps(sv)}
    ya = [x for x in aap.get("inventory_sources/", inventory=inv["id"]).get("results", []) if x.get("source") == p["fuente"]]
    if ya:
        aap.patch(f"inventory_sources/{ya[0]['id']}/", {"credential": cred["id"], "source_vars": json.dumps(sv)})
    else:
        aap.post("inventory_sources/", datos)


GITEA_INTERNO = "http://192.168.122.1:3000"


def _conectar_aap(aap, org, cliente, nombre, inv, v, p, f):
    """Lo que toda infraestructura tiene en AAP además de su credencial e inventario: su nodo de ejecución (grupo de
    instancias propio), sus secretos en la bóveda y su repositorio Git de configuración como proyecto. Devuelve lo que
    se registró, para mostrarlo en la ficha de la infraestructura."""
    import os
    import re
    import requests
    from .ejecucion import slug
    out = {}
    ee = (f.get("ee_host") or "").strip()   # opcional: se asume que AAP llega directo a la infraestructura
    if ee and not re.fullmatch(r"[A-Za-z0-9.-]{3,120}", ee):
        raise ValueError("El nodo de ejecución debe ser un nombre de máquina o una IP")
    s_ = slug(nombre)
    if ee:
        ig = (aap.get("instance_groups/", name=f"Infra · {nombre}").get("results") or [None])[0] or aap.post("instance_groups/", {"name": f"Infra · {nombre}"})
        try:   # AAP lo registra y entrega el paquete de instalación; hasta instalarlo, el inventario sigue usando el plano de control
            inst = aap.post("instances/", {"hostname": ee, "node_type": "execution", "listener_port": 27199, "peers_from_control_nodes": True})
            aap.post(f"instance_groups/{ig['id']}/instances/", {"id": inst["id"]})
            out["nodo de ejecución"] = f"{ee} · registrado en AAP, por instalar (paquete de instalación en AAP › Instancias)"
        except Exception:  # noqa: BLE001 - instalaciones que no aceptan registrar nodos por API: se instala con el instalador de AAP
            out["nodo de ejecución"] = f"{ee} · por instalar con el instalador de AAP (grupo de instancias «Infra · {nombre}»)"
    if f.get("usar_boveda"):
        from .boveda import escribir
        sec = {c: v[c] for c, _, es_sec, _ in p["campos"] if es_sec and v.get(c)}
        out["bóveda"] = f"faceops/infra/{s_}" if sec and escribir(f"infra/{s_}", sec) else "secretos solo en la credencial cifrada de AAP"
    if f.get("crear_repo"):
        tok = os.environ.get("FACEOPS_GITEA_TOKEN")
        repo = f"infra-{s_}"
        try:
            if tok:
                h = {"Authorization": f"token {tok}"}
                if requests.get(f"{GITEA_INTERNO}/api/v1/repos/invoke/{repo}", headers=h, timeout=8).status_code == 404:
                    requests.post(f"{GITEA_INTERNO}/api/v1/orgs/invoke/repos", headers=h, timeout=8, json={
                        "name": repo, "auto_init": True, "default_branch": "main", "private": True,
                        "description": f"Configuración como código de {nombre} (FaceOps)"}).raise_for_status()
                scm = (aap.get("credentials/", name="Gitea · lectura de playbooks").get("results") or [None])[0]
                pr = (aap.get("projects/", name=f"Infra · {nombre}").get("results") or [None])[0] or aap.post("projects/", dict(
                    {"name": f"Infra · {nombre}", "organization": org, "scm_type": "git", "scm_url": f"{GITEA_INTERNO}/invoke/{repo}.git",
                     "scm_branch": "main", "description": f"Configuración como código de {nombre}"}, **({"credential": scm["id"]} if scm else {})))
                out["repositorio Git"] = f"invoke/{repo} · proyecto AAP «{pr['name']}»"
        except Exception as x:  # noqa: BLE001 - la infraestructura queda igual; el repositorio se puede crear después
            out["repositorio Git"] = f"no se pudo crear: {str(x)[:120]}"
    return out


def agregar_fisico(usuario, cliente, f):
    """Servidor físico de a uno: si ya hay un grupo de ese fabricante en ese centro de datos, se agrega ahí (mismo inventario
    y credenciales en AAP); si no, nace el grupo «Fabricante · centro de datos»."""
    from .aap import AAP
    fab, modelo = (f.get("fabricante") or "").strip(), (f.get("modelo") or "").strip()
    srv, ip, bmc = (f.get("srv_nombre") or "").strip(), (f.get("srv_ip") or "").strip(), (f.get("srv_bmc") or "").strip()
    centro = (f.get("centro_datos") or "").strip()
    if fab not in FABRICANTES or not srv or not ip or not centro:
        raise ValueError("Elige fabricante y modelo, y escribe el centro de datos, el nombre del servidor y su IP")
    grupo = next((x for x in q("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='fisico'", cliente["id"])
                  if (j(x["detalle"], {}).get("fabricante") or "") == fab and (x.get("centro_datos") or "") == centro), None)
    servidor = {"nombre": srv, "ip": ip, "bmc": bmc, "modelo": modelo}
    if not grupo:   # primer servidor de este fabricante aquí: nace su grupo (credenciales e inventario en AAP)
        g_ = dict(f.items()) if hasattr(f, "items") else dict(f)
        g_.update({"tipo": "fisico", "nombre": f"{fab} · {centro}", "equipos": f"{srv}, {ip}" + (f", {bmc}" if bmc else ""),
                   "bmc_tipo": f.get("bmc_tipo") or "redfish"})
        nombre = incorporar(usuario, cliente, g_)
        x = q1("SELECT * FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente["id"], nombre)
        det = j(x["detalle"], {})
        det.update({"fabricante": fab, "gestion": FABRICANTES[fab][0], "servidores": [servidor], "equipos": 1})
        det.pop("modelo", None)
        ej("UPDATE infraestructuras SET detalle=? WHERE id=?", json.dumps(det, ensure_ascii=False), x["id"])
        return nombre, True
    det = j(grupo["detalle"], {})
    if any(s_.get("nombre") == srv for s_ in det.get("servidores", [])):
        raise ValueError(f"{srv} ya está en {grupo['nombre']}")
    aap = AAP.de_cliente(cliente)
    inv = (aap.get("inventories/", name=grupo["inventario"]).get("results") or [None])[0]
    if inv:
        hv = {"ansible_host": ip, "faceops_forma": "física", "faceops_categoria": "servidor", "faceops_fabricante": fab, "faceops_modelo": modelo}
        if bmc:
            hv.update({"bmc_direccion": bmc, "bmc_tipo": det.get("bmc_tipo") or "redfish"})
        aap.post("hosts/", {"name": srv, "inventory": inv["id"], "variables": json.dumps(hv)})
    det.setdefault("servidores", []).append(servidor)
    det["equipos"] = len(det["servidores"])
    ej("UPDATE infraestructuras SET detalle=? WHERE id=?", json.dumps(det, ensure_ascii=False), grupo["id"])
    auditar(usuario["usuario"], cliente["id"], "agregar servidor físico", grupo["nombre"], servidor)
    return grupo["nombre"], False


def incorporar(usuario, cliente, f):
    """Crea la credencial (con los secretos del formulario) y el inventario en AAP; registra la infraestructura."""
    from .aap import AAP
    tipo, nombre = f.get("tipo", ""), (f.get("nombre") or "").strip()
    p = PROVEEDORES.get(tipo)
    if not p or not nombre:
        raise ValueError("Elige el tipo de infraestructura y escribe un nombre")
    if q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente["id"], nombre):
        raise ValueError("Ya existe una infraestructura con ese nombre")
    v = _valores(p, f)
    centro = (f.get("centro_datos") or "").strip() or (CENTRO_NUBE.get(tipo, p["nombre"]) if p["ambito"] == "nube" else "")
    if not centro:
        raise ValueError("Indica el centro de datos donde está esta infraestructura")
    fisicos = _equipos_fisicos(f.get("equipos") or v.get("equipos") or "") if tipo == "fisico" else []
    aap = AAP.de_cliente(cliente)
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    cred = _credencial(aap, org, tipo, nombre, v)
    bmc = _credencial_bmc(aap, org, nombre, v) if tipo == "fisico" else None
    inv = aap.post("inventories/", {"name": f"Infra · {nombre}", "organization": org, "description": f"Equipos de {nombre} ({p['nombre']})"})
    _fuente(aap, inv, nombre, tipo, cred, v)
    acc = cred if tipo in ("kvm", "fisico") else _credencial_maquinas(aap, org, nombre, v.get("acc_usuario"), v.get("acc_llave"), v.get("acc_clave"))
    if acc:
        plan_acceso(cliente["id"], inv["name"], acc["name"])
    if tipo == "kvm":
        aap.post("hosts/", {"name": f"anfitrion-{nombre.lower().replace(' ', '-')[:30]}", "inventory": inv["id"],
                            "variables": json.dumps({"ansible_host": v["anfitrion"], "faceops_categoria": "servidor", "faceops_skills": ["kvm"]})})
    for e in fisicos:
        hv = {"ansible_host": e["ip"], "faceops_forma": "física", "faceops_categoria": "servidor"}
        if e["bmc"]:
            hv.update({"bmc_direccion": e["bmc"], "bmc_tipo": v.get("bmc_tipo") or "redfish"})
        aap.post("hosts/", {"name": e["nombre"], "inventory": inv["id"], "variables": json.dumps(hv)})
    publicos = {c: v[c] for c, _, sec, _ in p["campos"] if not sec and v.get(c) and c not in ("equipos", "srv_nombre", "srv_ip", "srv_bmc")}
    publicos.update(_conectar_aap(aap, org, cliente, nombre, inv, v, p, f))
    if p["naturaleza"] != "contenedores":
        publicos["acceso a máquinas"] = acc["name"] if acc else "pendiente: «Incorporar máquinas a FaceOps»"
    if fisicos:
        publicos["equipos"] = len(fisicos)
    ej("INSERT INTO infraestructuras(cliente_id, nombre, tipo, region, detalle, credencial, inventario, ubicacion, estado, creado, centro_datos) "
       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
       cliente["id"], nombre, tipo, v.get("region") or v.get("zona") or v.get("sala") or "", json.dumps(publicos, ensure_ascii=False),
       cred["name"], inv["name"], nombre, "sin probar", ahora(), centro)
    # sus equipos entran al inventario de FaceOps con esta infraestructura como ubicación
    cl = j(cliente["clasificacion"], {})
    cl[inv["name"]] = {"proveedor": tipo, "ubicacion": nombre, "ambiente": "produccion", "forma": "física" if tipo == "fisico" else "virtual"}
    ej("UPDATE clientes SET clasificacion=? WHERE id=?", json.dumps(cl, ensure_ascii=False), cliente["id"])
    auditar(usuario["usuario"], cliente["id"], "incorporar infraestructura", nombre,
            {"tipo": tipo, "credencial": cred["name"], "inventario": inv["name"], "bmc": bool(bmc), "equipos": len(fisicos)})
    return nombre


def conectar(usuario, cliente, iid, f):
    """Carga (o renueva) la credencial de acceso de una infraestructura ya registrada."""
    from .aap import AAP
    x = q1("SELECT * FROM infraestructuras WHERE id=? AND cliente_id=?", iid, cliente["id"])
    if not x:
        raise ValueError("No existe")
    p = PROVEEDORES[x["tipo"]]
    det = j(x["detalle"], {})
    datos = {c: (f.get(c) or det.get(c) or "") for c, _, _, _ in p["campos"]}
    if x["tipo"] == "fisico" and not f.get("equipos"):
        datos["equipos"] = "x, 0.0.0.0"  # los equipos ya están en su inventario
    v = _valores(p, datos)
    aap = AAP.de_cliente(cliente)
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    cred = _credencial(aap, org, x["tipo"], x["nombre"], v, existente=x["credencial"])
    if x["tipo"] == "fisico":
        _credencial_bmc(aap, org, x["nombre"], v)
    inv = (aap.get("inventories/", name=x["inventario"]).get("results") or [None])[0]
    if inv:
        _fuente(aap, inv, x["nombre"], x["tipo"], cred, v)
    acc = cred if x["tipo"] in ("kvm", "fisico") else _credencial_maquinas(aap, org, x["nombre"], v.get("acc_usuario"), v.get("acc_llave"), v.get("acc_clave"))
    if acc:
        plan_acceso(cliente["id"], x["inventario"], acc["name"])
        det["acceso a máquinas"] = acc["name"]
    publicos = dict(det, **{c: v[c] for c, _, sec, _ in p["campos"] if not sec and v[c] and c != "equipos"})
    ej("UPDATE infraestructuras SET credencial=?, detalle=?, estado='sin probar', mensaje='Credencial cargada en AAP: falta «Probar acceso»' WHERE id=?",
       cred["name"], json.dumps(publicos, ensure_ascii=False), iid)
    auditar(usuario["usuario"], cliente["id"], "conectar infraestructura", x["nombre"], {"credencial": cred["name"]})
    return x["nombre"]


def editar(usuario, cliente, iid, f):
    """«Editar configuración»: cambia cualquier dato de una infraestructura ya registrada. Los no secretos se guardan en
    FaceOps; la credencial de AAP se actualiza cifrada y un secreto dejado vacío conserva el que AAP ya tiene (no se lee
    nunca: se manda «$encrypted$»). Después se vuelve a probar el acceso desde la vista."""
    from .aap import AAP
    x = q1("SELECT * FROM infraestructuras WHERE id=? AND cliente_id=?", iid, cliente["id"])
    if not x:
        raise ValueError("No existe")
    p = PROVEEDORES[x["tipo"]]
    det = j(x["detalle"], {})
    nombre = (f.get("nombre") or "").strip() or x["nombre"]
    if nombre != x["nombre"] and q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre=? AND id<>?", cliente["id"], nombre, iid):
        raise ValueError("Ya existe otra infraestructura con ese nombre")
    aap = AAP.de_cliente(cliente)
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    previa = (aap.get("credentials/", name=x["credencial"]).get("results") or [None])[0] if x["credencial"] else None
    v, faltan = {}, []
    for c, et, sec, _ in p["campos"]:
        val = (f.get(c) or "").strip()
        if sec and not val and c not in ACCESO_CAMPOS:
            val = ENCRIPTADO if previa else ""   # vacío = conservar el secreto actual
        v[c] = val
        if c in p["obligatorios"] and not val and c not in ACCESO_CAMPOS:
            faltan.append(et)
    if faltan:
        raise ValueError("Faltan: " + ", ".join(faltan))
    if x["tipo"] == "fisico":   # los servidores ya están en su inventario
        v["equipos"] = det.get("equipos", "")
    cred = _credencial(aap, org, x["tipo"], x["nombre"], v, existente=x["credencial"], previo=(previa or {}).get("inputs"))
    if x["tipo"] == "fisico" and v.get("bmc_clave") != ENCRIPTADO:
        _credencial_bmc(aap, org, x["nombre"], v)
    inv = (aap.get("inventories/", name=x["inventario"]).get("results") or [None])[0] if x["inventario"] else None
    if inv:
        _fuente(aap, inv, x["nombre"], x["tipo"], cred, v)
    if p.get("acceso"):   # acceso a las máquinas: solo si se escribió una llave o clave nueva, o cambió el usuario
        acc_nom = f"Infra · {x['nombre']} · máquinas"
        acc_prev = (aap.get("credentials/", name=acc_nom).get("results") or [None])[0]
        if v.get("acc_usuario") and (v.get("acc_llave") or v.get("acc_clave")):
            acc = _credencial_maquinas(aap, org, x["nombre"], v["acc_usuario"], v.get("acc_llave"), v.get("acc_clave"))
        elif acc_prev and v.get("acc_usuario") and v["acc_usuario"] != det.get("acc_usuario"):
            ins_ = dict(acc_prev.get("inputs") or {}, username=v["acc_usuario"])
            acc = aap.patch(f"credentials/{acc_prev['id']}/", {"inputs": ins_})
        else:
            acc = acc_prev
        if acc:
            plan_acceso(cliente["id"], x["inventario"], acc["name"])
            det["acceso a máquinas"] = acc["name"]
    for c, _, sec, _ in p["campos"]:
        if not sec and c != "equipos":
            if v[c]:
                det[c] = v[c]
            else:
                det.pop(c, None)
    region = (f.get("region_nombre") or "").strip() or v.get("region") or v.get("zona") or x["region"]
    centro = (f.get("centro_datos") or "").strip() or x["centro_datos"]
    ej("UPDATE infraestructuras SET nombre=?, region=?, centro_datos=?, credencial=?, detalle=?, estado='sin probar', "
       "mensaje='Configuración actualizada: probando acceso' WHERE id=?",
       nombre, region, centro, cred["name"], json.dumps(det, ensure_ascii=False), iid)
    _CACHE_NUBE.pop((cliente["id"], iid), None)
    auditar(usuario["usuario"], cliente["id"], "editar infraestructura", nombre,
            {"credencial": cred["name"], "secretos_nuevos": sorted(c for c, _, sec, _ in p["campos"] if sec and v.get(c) and v[c] != ENCRIPTADO),
             "nombre_anterior": x["nombre"] if nombre != x["nombre"] else None})
    return nombre



# Datos generales del anfitrión KVM que «Probar acceso» deja en detalle.info (una línea «INF clave=valor» por dato)
INFO_KVM = "; ".join(f'echo "INF {k}=$({c})"' for k, c in (
    ("nombre", "hostname -f"), ("so", ". /etc/os-release; echo $PRETTY_NAME"), ("kernel", "uname -r"),
    ("cpu", "lscpu | sed -n 's/^Model name: *//p' | head -1"), ("sockets", "lscpu -p=socket | grep -v '^#' | sort -u | wc -l"),
    ("hilos", "nproc"), ("virt", "grep -qE 'vmx|svm' /proc/cpuinfo && echo si || echo no"),
    ("libvirt", "sudo -n virsh version --daemon 2>/dev/null | sed -n 's/^Running against daemon: *//p'"),
    ("qemu", "sudo -n virsh version 2>/dev/null | sed -n 's/^Running hypervisor: *//p'"), ("desde", "uptime -s"),
    ("redes", "sudo -n virsh net-list --name 2>/dev/null | xargs"), ("pools", "sudo -n virsh pool-list --name 2>/dev/null | xargs"),
    ("vms", "sudo -n virsh list --all --name 2>/dev/null | grep -c ."), ("vms_on", "sudo -n virsh list --name 2>/dev/null | grep -c ."),
    ("autoinicio", "sudo -n virsh list --all --autostart --name 2>/dev/null | xargs")))

def probar(usuario, cliente, iid):
    """Prueba el acceso: en las nubes sincroniza su inventario (lista sus VM); en KVM, un ping de Ansible al anfitrión."""
    import time
    from .aap import AAP, ErrorAAP
    x = q1("SELECT * FROM infraestructuras WHERE id=? AND cliente_id=?", iid, cliente["id"])
    if not x:
        raise ValueError("No existe")
    aap = AAP.de_cliente(cliente)
    inv = (aap.get("inventories/", name=x["inventario"]).get("results") or [None])[0]
    estado, msg = "error", ""
    try:
        if x["tipo"] in ("openshift", "openshift-virt"):
            import requests
            api = j(x["detalle"], {}).get("api", "")
            try:
                ver = requests.get(api.rstrip("/") + "/version", timeout=10, verify=False).json().get("gitVersion", "?")
            except (requests.RequestException, ValueError) as e:
                raise ErrorAAP(f"La API {api} no responde ({str(e)[:120]})")
            est = "conectada" if x["credencial"] else "sin credencial"
            m = f"API responde (Kubernetes {ver}) · " + (f"token en la credencial «{x['credencial']}» de AAP" if x["credencial"] else "falta cargar su token con «Conectar»")
            ej("UPDATE infraestructuras SET estado=?, verificado=?, mensaje=? WHERE id=?", est, ahora(), m, iid)
            return est, m
        if not inv:
            raise ErrorAAP(f"No existe el inventario «{x['inventario']}» en AAP")
        fuentes = aap.get("inventory_sources/", inventory=inv["id"]).get("results") or []
        if x["tipo"] in FUENTE_NUBE:   # nube: sin credencial o sin fuente dinámica no hay nada que probar (un ping a un inventario vacío no prueba la nube)
            _CACHE_NUBE.pop((cliente["id"], iid), None)
            r_ = estado_nube(aap, dict(x), cliente["id"], guardar=False, ttl=0)
            fuentes = [s_ for s_ in fuentes if s_.get("source") == FUENTE_NUBE[x["tipo"]] or (r_["cred"] and s_.get("credential") == r_["cred"]["id"])]
            if not r_["cred"] or not fuentes:
                estado = "sin credencial" if not r_["cred"] else "error"
                raise ErrorAAP(r_["mensaje"])
        from .confianza import autorizar
        autorizar(cliente, f"Probar acceso · {x['nombre']}", f"infra:{x['id']}", {"inventario": x["inventario"]}, usuario=usuario["usuario"], origen="faceops")
        if fuentes:
            u = aap.post(f"inventory_sources/{fuentes[0]['id']}/update/", {})
            ruta = f"inventory_updates/{u.get('inventory_update') or u.get('id')}/"
        else:
            cred = (aap.get("credentials/", name=x["credencial"]).get("results") or [None])[0]
            u = aap.post(f"inventories/{inv['id']}/ad_hoc_commands/", {"module_name": "ping", "credential": cred["id"] if cred else None, "limit": "all"})
            ruta = f"ad_hoc_commands/{u['id']}/"
        for _ in range(40):
            st = aap.get(ruta)["status"]
            if st in ("successful", "failed", "error", "canceled"):
                break
            time.sleep(3)
        hosts = aap.get(f"inventories/{inv['id']}/").get("total_hosts", 0)
        estado = "conectada" if st == "successful" else "error"
        if st == "successful" and not fuentes:   # anfitrión propio (KVM): capacidad total del equipo (cores físicos, memoria, disco de VMs)
            try:
                ruta_ = shlex.quote(j(x["detalle"], {}).get("pool") or "/var/lib/libvirt/images")   # disco libre del pool propio (Valparaíso: dc-valpo)
                cmd = ("echo CAP $(lscpu -p=core,socket | grep -v '^#' | sort -u | wc -l) "
                       "$(free -m | awk '/^Mem/{print $2, $7}') "
                       f"$(df -BG --output=size,avail {ruta_} 2>/dev/null | tail -1 | tr -d G); " + INFO_KVM)
                c_ = aap.post(f"inventories/{inv['id']}/ad_hoc_commands/", {"module_name": "shell", "module_args": cmd,
                                                                            "credential": cred["id"] if cred else None, "limit": "all"})
                for _ in range(30):
                    if aap.get(f"ad_hoc_commands/{c_['id']}/")["status"] in ("successful", "failed", "error", "canceled"):
                        break
                    time.sleep(3)
                for ev in aap.get(f"ad_hoc_commands/{c_['id']}/events/", event="runner_on_ok").get("results") or []:
                    sal_ = (ev.get("event_data", {}).get("res", {}).get("stdout") or "").splitlines()
                    lin = next((l_ for l_ in sal_ if l_.startswith("CAP ")), "")
                    v_ = lin.split()[1:]
                    if len(v_) >= 3 and all(t_.isdigit() for t_ in v_[:3]):
                        v_ = v_[:3] + (v_[3:5] if len(v_) >= 5 and all(t_.isdigit() for t_ in v_[3:5]) else [])   # sin df legible: disco «sin medición», no 0
                        d_ = j(x["detalle"], {})
                        d_["info"] = dict(l_[4:].split("=", 1) for l_ in sal_ if l_.startswith("INF ") and "=" in l_)   # datos generales del anfitrión
                        d_["info"]["medido"] = ahora()
                        d_["capacidad"] = {"cores": int(v_[0]), "ram_gb": round(int(v_[1]) / 1024), "ram_disp_gb": round(int(v_[2]) / 1024),
                                           "disco_gb": int(v_[3]) if len(v_) > 3 else None, "disco_disp_gb": int(v_[4]) if len(v_) > 4 else None, "medido": ahora()}
                        ej("UPDATE infraestructuras SET detalle=? WHERE id=?", json.dumps(d_, ensure_ascii=False), iid)
                        break
            except ErrorAAP:
                pass
        msg = f"{hosts} equipo(s) en su inventario de AAP" if st == "successful" else f"AAP: {st}"
    except ErrorAAP as e:
        msg = str(e)[:300]
    _CACHE_NUBE.pop((cliente["id"], iid), None)
    ej("UPDATE infraestructuras SET estado=?, verificado=?, mensaje=? WHERE id=?", estado, ahora(), msg, iid)
    auditar(usuario["usuario"], cliente["id"], "probar infraestructura", x["nombre"], {"estado": estado, "mensaje": msg})
    return estado, msg


# ------------------------------------------------------------------ nubes: estado real de la conexión y lo que AAP ve en ellas
FUENTE_NUBE = {"gcp": "gce", "aws": "ec2", "azure": "azure_rm"}
_CACHE_NUBE = {}   # (cliente, infraestructura) → (instante, resultado): la lista y el detalle no consultan AAP en cada clic
def ayudas_contexto(x, con=None):
    """Ayudas del formulario de conexión con cada enlace apuntando a la página exacta donde se crea u obtiene ese dato,
    usando lo que ya se sabe de la conexión (proyecto, cuenta de servicio, usuario IAM, aplicación de Azure). Nunca usa secretos."""
    from urllib.parse import quote
    p = PROVEEDORES.get(x.get("tipo")) or {}
    ay = {k: list(v) for k, v in (p.get("ayudas") or {}).items()}
    d = x.get("detalle_d") or j(x.get("detalle"), {}) or {}
    cred = (con or {}).get("cred") or {}
    q_ = lambda v: quote(str(v), safe="@.-_")
    if x.get("tipo") == "gcp":
        pr = d.get("proyecto") or cred.get("proyecto") or ""
        qp = f"?project={q_(pr)}" if pr else ""
        sa = cred.get("cuenta_servicio") or d.get("cuenta_servicio") or ""
        if "cuenta" in ay:
            if sa:
                ay["cuenta"][1] = f"Cuenta de servicio {sa} › Claves › Agregar clave › Crear clave nueva › JSON; pega el archivo completo"
                ay["cuenta"][2] = f"https://console.cloud.google.com/iam-admin/serviceaccounts/details/{q_(sa)}/keys{qp}"
            else:
                ay["cuenta"][2] = f"https://console.cloud.google.com/iam-admin/serviceaccounts/create{qp}"
        if "proyecto" in ay and pr:
            ay["proyecto"][2] = f"https://console.cloud.google.com/home/dashboard{qp}"
        if "red" in ay and pr:
            ay["red"][2] = f"https://console.cloud.google.com/networking/networks/list{qp}"
    elif x.get("tipo") == "aws":
        us, rg = d.get("usuario_iam") or "", d.get("region") or ""
        if us:
            for k in ("access_key", "secret_key"):
                if k in ay:
                    ay[k][1] = f"Usuario IAM {us} › Credenciales de seguridad › Crear clave de acceso" + (" (la secreta se muestra una sola vez)" if k == "secret_key" else "")
                    ay[k][2] = f"https://console.aws.amazon.com/iam/home#/users/details/{q_(us)}?section=security_credentials"
            if "usuario_iam" in ay:
                ay["usuario_iam"][2] = f"https://console.aws.amazon.com/iam/home#/users/details/{q_(us)}"
        if "vpc" in ay and rg:
            ay["vpc"][2] = f"https://{q_(rg)}.console.aws.amazon.com/vpcconsole/home?region={q_(rg)}#vpcs:"
    elif x.get("tipo") == "azure":
        app_, sub_ = d.get("cliente") or "", d.get("suscripcion") or ""
        base = "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps"
        if app_:
            if "secreto" in ay:
                ay["secreto"][2] = f"{base}/ApplicationMenuBlade/~/Credentials/appId/{q_(app_)}"
            if "cliente" in ay:
                ay["cliente"][2] = f"{base}/ApplicationMenuBlade/~/Overview/appId/{q_(app_)}"
        else:
            for k in ("cliente", "secreto"):
                if k in ay:
                    ay[k][2] = f"{base}/CreateApplicationBlade"
        if "suscripcion" in ay and sub_:
            ay["suscripcion"][2] = f"https://portal.azure.com/#@/resource/subscriptions/{q_(sub_)}/overview"
    return dict(p, ayudas={k: tuple(v) for k, v in ay.items()})


DATO_CUENTA = {"gcp": ("proyecto", "Proyecto"), "aws": ("cuenta", "Cuenta"), "azure": ("suscripcion", "Suscripción")}


def _fin(u):
    return str(u or "").rstrip("/").rsplit("/", 1)[-1]


def _motivo_falla(aap, upd_id):
    """Última línea útil del error de una sincronización de inventario (sin secretos: solo el texto del error)."""
    try:
        u = aap.get(f"inventory_updates/{upd_id}/")
    except Exception:  # noqa: BLE001
        return ""
    try:   # la salida trae el error real del proveedor (el traceback solo dice «No inventory was parsed»)
        sal = re.sub(r"\x1b\[[0-9;]*m", "", aap.get(f"inventory_updates/{upd_id}/stdout/", format="json").get("content", "") or "")
    except Exception:  # noqa: BLE001
        sal = ""
    plano = re.sub(r"\s*\n\s*", "", sal)
    m_ = re.search(r"API has not been used in project ([\w-]+) before or it is disabled", plano)
    if m_:
        return (f"la API de Compute Engine está deshabilitada en el proyecto {m_.group(1)}: actívala en "
                f"https://console.cloud.google.com/apis/library/compute.googleapis.com?project={m_.group(1)} y vuelve a validar")
    if re.search(r"invalid_grant|Invalid JWT|account not found", plano, re.I):
        return "Google rechazó la llave de la cuenta de servicio (revocada, vencida o de otra cuenta): genera una llave nueva"
    if re.search(r"PERMISSION_DENIED|Required 'compute\.\w+\.list' permission|forbidden", plano, re.I):
        return "la cuenta de servicio no tiene permiso para listar las máquinas: dale el rol Compute Viewer en el proyecto"
    if re.search(r"AuthFailure|InvalidClientTokenId|SignatureDoesNotMatch", plano):
        return "AWS rechazó la clave de acceso (inválida o desactivada)"
    if re.search(r"AADSTS\d+", plano):
        return "Azure rechazó el service principal: " + re.search(r"AADSTS\d+[^.]*", plano).group(0)[:160]
    lineas = [l_.strip() for l_ in ((u.get("result_traceback") or "") + "\n" + (u.get("job_explanation") or "")).splitlines() if l_.strip()]
    lineas = [l_ for l_ in lineas if "private_key" not in l_.lower() and "BEGIN" not in l_]
    return (lineas[-1] if lineas else "")[:220]


def conocido_(x, err=None):
    """AAP no respondió: la nube conserva su último estado verificado (guardado en FaceOps) y su hora; solo una verificación
    que corrió y en la que el proveedor respondió error la marca con error. x["aap_caido"] lo avisa en la lista y la ficha."""
    from .aap import AAPNoDisponible
    g_ = q1("SELECT estado, mensaje, verificado FROM infraestructuras WHERE id=?", x["id"]) or {}
    x["estado"] = g_.get("estado") or x.get("estado") or "sin verificar"
    x["verificado"] = g_.get("verificado") or x.get("verificado")
    x["aap_caido"] = True
    x["mensaje"] = ("AAP no disponible" if (err is None or isinstance(err, AAPNoDisponible)) else f"No se pudo consultar AAP ({str(err)[:120]})") + \
        ": se muestra el último estado conocido"


def estado_nube(aap, x, cliente_id, guardar=True, ttl=60):
    """Estado de la conexión de una nube leído en AAP en este momento (no el valor guardado): credencial, inventario,
    fuente dinámica y su última sincronización. Deja el resultado en la infraestructura para que el resto de FaceOps lo vea."""
    import time
    from .aap import ErrorAAP
    k_ = (cliente_id, x["id"])
    if k_ in _CACHE_NUBE and time.time() - _CACHE_NUBE[k_][0] < ttl:
        return _CACHE_NUBE[k_][1]
    r = {"estado": "sin verificar", "mensaje": "", "cred": None, "inv": None, "fuente": None, "ultima": None, "hosts": None}
    try:
        cred = (aap.get("credentials/", name=x["credencial"]).get("results") or [None])[0] if x.get("credencial") else None
        inv = (aap.get("inventories/", name=x["inventario"]).get("results") or [None])[0] if x.get("inventario") else None
        fuentes = aap.get("inventory_sources/", inventory=inv["id"]).get("results") or [] if inv else []
        fte = next((s for s in fuentes if s.get("source") == FUENTE_NUBE.get(x["tipo"])), None) or \
            next((s for s in fuentes if cred and s.get("credential") == cred["id"]), None)
        if cred:
            r["cred"] = {"nombre": cred["name"], "id": cred["id"], "tipo": (cred.get("summary_fields") or {}).get("credential_type", {}).get("name", ""),
                         "proyecto": (cred.get("inputs") or {}).get("project") or (cred.get("inputs") or {}).get("subscription") or "",
                         # cuenta de servicio de GCP (client_email, no es secreto): para enlazar directo a sus claves
                         "cuenta_servicio": (cred.get("inputs") or {}).get("username", "") if x["tipo"] == "gcp" else "",
                         "modificada": cred.get("modified")}
        if inv:
            r["inv"] = {"nombre": inv["name"], "id": inv["id"], "hosts": inv.get("total_hosts", 0)}
            r["hosts"] = inv.get("total_hosts", 0)
        if fte:
            lj = (fte.get("summary_fields") or {}).get("last_job") or {}
            sv = fte.get("source_vars") or ""
            r["fuente"] = {"nombre": fte["name"], "id": fte["id"], "tipo": fte.get("source"), "estado": fte.get("status"),
                           "ultima": fte.get("last_updated") or lj.get("finished"), "vars": sv[:300], "job": lj.get("id")}
            r["ultima"] = r["fuente"]["ultima"]
        cuenta = (x.get("detalle_d") or j(x.get("detalle"), {})).get(DATO_CUENTA.get(x["tipo"], ("", ""))[0], "")
        if not x.get("credencial") or not cred:
            r["estado"] = "sin credencial"
            r["mensaje"] = (f"No hay conexión: falta en AAP la credencial «{x['credencial']}»" if x.get("credencial") else "No hay conexión: no tiene credencial") + \
                (f" (cuenta de servicio del proyecto {cuenta})" if x["tipo"] == "gcp" and cuenta else "") + \
                ". Sin ella AAP no puede leer la nube; cárgala con «Conectar»"
        elif not inv:
            r["estado"], r["mensaje"] = "error", f"No existe en AAP el inventario «{x['inventario']}»"
        elif not fte:
            r["estado"] = "error"
            r["mensaje"] = f"El inventario «{inv['name']}» no tiene fuente dinámica ({FUENTE_NUBE.get(x['tipo'])}); se crea al volver a «Conectar»"
        elif fte.get("status") in ("successful",):
            r["estado"] = "conectada"
            r["mensaje"] = f"AAP lee la nube: {r['hosts']} máquina(s) en su inventario"
        elif fte.get("status") in ("failed", "error"):
            r["estado"] = "error"
            jid_ = r["fuente"]["job"] or ((aap.get("inventory_updates/", inventory_source=r["fuente"]["id"], order_by="-id", page_size=1).get("results") or [{}])[0].get("id"))
            mot = _motivo_falla(aap, jid_) if jid_ else ""
            r["mensaje"] = "La última sincronización con la nube falló" + (f": {mot}" if mot else "")
        elif fte.get("status") in ("running", "pending", "waiting"):
            r["estado"], r["mensaje"] = "sin probar", "Sincronizando con la nube en AAP…"
        else:
            r["estado"], r["mensaje"] = "sin probar", "Credencial y fuente listas en AAP; nunca se ha sincronizado: falta «Probar acceso»"
    except ErrorAAP as e:   # falla de AAP, no de la nube: se mantiene el último estado conocido con su hora
        conocido_(x, e)
        r.update(estado=x["estado"], mensaje=x["mensaje"], aap_caido=True)
        _CACHE_NUBE[k_] = (time.time(), r)
        return r
    if guardar and r["estado"] != "sin verificar" and (x.get("estado") != r["estado"] or x.get("mensaje") != r["mensaje"]):
        ej("UPDATE infraestructuras SET estado=?, mensaje=?, verificado=? WHERE id=?", r["estado"], r["mensaje"], ahora(), x["id"])
    x["estado"], x["mensaje"] = r["estado"], r["mensaje"]
    _CACHE_NUBE[k_] = (time.time(), r)
    return r


def maquinas_nube(aap, inv_id):
    """Máquinas que la fuente dinámica de AAP trae de la nube, con lo que informa el proveedor (gcp_compute, aws_ec2,
    azure_rm): estado, tipo, zona, interfaces, IPs, discos y etiquetas. Solo lectura de AAP."""
    out = []
    for h in aap.todos(f"inventories/{inv_id}/hosts/"):
        try:
            v = json.loads(h.get("variables") or "{}")
        except ValueError:
            import yaml
            v = yaml.safe_load(h.get("variables") or "") or {}
        nics = v.get("networkInterfaces") or []
        zona = _fin(v.get("zone")) or ((v.get("placement") or {}).get("availability_zone", "") if isinstance(v.get("placement"), dict) else "")
        est = v.get("status") or ((v.get("state") or {}).get("name") if isinstance(v.get("state"), dict) else "") or v.get("powerstate") or ""
        reg = zona.rsplit("-", 1)[0] if zona.count("-") >= 2 else (v.get("location") or "")
        pub = [ac.get("natIP") for n in nics for ac in (n.get("accessConfigs") or []) if ac.get("natIP")] or \
            [x_ for x_ in [v.get("public_ip_address")] if x_]
        lbl = v.get("labels") or {}
        out.append({"nombre": h["name"], "host_id": h["id"], "estado": est,
                    "tipo": _fin(v.get("machineType")) or v.get("instance_type") or v.get("virtual_machine_size") or "",
                    "zona": zona or "", "region": reg, "ip": (nics[0].get("networkIP") if nics else "") or v.get("private_ip_address") or v.get("ansible_host") or "",
                    "publicas": pub, "creada": v.get("creationTimestamp") or v.get("launch_time") or "",
                    "redes": [{"vpc": _fin(n.get("network")), "subred": _fin(n.get("subnetwork")),
                               "region": (n.get("subnetwork") or "").split("/regions/")[-1].split("/")[0] if "/regions/" in (n.get("subnetwork") or "") else reg,
                               "ip": n.get("networkIP")} for n in nics],
                    "discos": [{"nombre": d.get("deviceName") or _fin(d.get("source")), "gb": d.get("diskSizeGb"), "tipo": d.get("type") or "",
                                "arranque": bool(d.get("boot")), "interfaz": d.get("interface") or ""} for d in (v.get("disks") or [])],
                    "etiquetas": lbl if isinstance(lbl, dict) else {},
                    "cluster": next((k[len("kubernetes-io-cluster-"):] for k in (lbl if isinstance(lbl, dict) else {}) if k.startswith("kubernetes-io-cluster-")), "")})
    return out
