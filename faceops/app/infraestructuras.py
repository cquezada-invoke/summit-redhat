"""Infraestructuras del cliente: dónde viven sus equipos (centro de datos on-premise, VMware, GCP, AWS, Azure…) y cómo
llega FaceOps a cada una. Al incorporar una, sus secretos van directo a una credencial cifrada de AAP (FaceOps no los
guarda), se crea su inventario en AAP (con fuente dinámica en las nubes) y sus equipos entran al inventario de FaceOps."""
import json

from .bd import ahora, auditar, ej, j, q, q1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS infraestructuras (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, nombre TEXT NOT NULL, tipo TEXT NOT NULL, region TEXT DEFAULT '',
  detalle TEXT DEFAULT '{}', credencial TEXT DEFAULT '', inventario TEXT DEFAULT '', ubicacion TEXT DEFAULT '',
  estado TEXT DEFAULT 'sin probar', verificado TEXT, mensaje TEXT DEFAULT '', creado TEXT, UNIQUE(cliente_id, nombre));
"""

# proveedor → nombre, ámbito, campos (var, etiqueta, secreto, tipo de control), tipo de credencial de AAP y fuente de inventario
OPCIONALES = {"red", "vpc", "datacenter", "bmc_tipo", "bmc_usuario", "bmc_clave", "sala", "carpeta"}
PROVEEDORES = {
    "gcp": {"nombre": "Google Cloud", "ambito": "nube", "fuente": "gce", "credencial_aap": "Google Compute Engine",
            "ayuda": "Cuenta de servicio con rol Compute Admin del proyecto (IAM → Cuentas de servicio → Claves → JSON)",
            "campos": [("proyecto", "ID del proyecto", False, "text"), ("zona", "Zona (ej. southamerica-west1-a)", False, "text"),
                       ("cuenta", "Cuenta de servicio (JSON completo)", True, "area"), ("red", "Red VPC", False, "text")]},
    "aws": {"nombre": "Amazon Web Services", "ambito": "nube", "fuente": "ec2", "credencial_aap": "Amazon Web Services",
            "ayuda": "Usuario IAM con acceso programático (EC2, VPC, Route 53 si se instala OpenShift)",
            "campos": [("access_key", "Access key ID", False, "text"), ("secret_key", "Secret access key", True, "password"),
                       ("region", "Región (ej. sa-east-1)", False, "text"), ("vpc", "VPC", False, "text")]},
    "azure": {"nombre": "Microsoft Azure", "ambito": "nube", "fuente": "azure_rm", "credencial_aap": "Microsoft Azure Resource Manager",
              "ayuda": "Service principal (App registration) con rol Contributor en la suscripción",
              "campos": [("suscripcion", "ID de suscripción", False, "text"), ("tenant", "Tenant ID", False, "text"),
                         ("cliente", "Client ID (App ID)", False, "text"), ("secreto", "Client secret", True, "password"),
                         ("region", "Región (ej. brazilsouth)", False, "text")]},
    "vmware": {"nombre": "VMware vSphere", "ambito": "on-premise", "fuente": "vmware", "credencial_aap": "VMware vCenter",
               "ayuda": "Usuario de vCenter con permisos sobre el datacenter (lectura para inventario; administrador de VM para crear)",
               "campos": [("host", "vCenter (FQDN o IP)", False, "text"), ("usuario", "Usuario (ej. faceops@vsphere.local)", False, "text"),
                          ("clave", "Clave", True, "password"), ("datacenter", "Datacenter", False, "text"), ("carpeta", "Carpeta de VM", False, "text")]},
    "kvm": {"nombre": "Hipervisor KVM", "ambito": "on-premise", "fuente": None, "credencial_aap": "Machine",
            "ayuda": "Anfitrión con libvirt; el usuario necesita sudo para virsh y virt-install",
            "campos": [("anfitrion", "Anfitrión KVM (IP o nombre)", False, "text"), ("usuario", "Usuario SSH", False, "text"),
                       ("llave", "Llave privada SSH", True, "area"), ("red", "Red de las VM (ej. 192.168.122.0/24)", False, "text")]},
    "fisico": {"nombre": "Máquinas físicas", "ambito": "on-premise", "fuente": None, "credencial_aap": "Machine",
               "ayuda": "Servidores bare metal: acceso SSH al sistema y, si tienen, gestión fuera de banda (iDRAC, iLO, XClarity) por Redfish o IPMI",
               "campos": [("equipos", "Equipos (uno por línea: nombre, IP del sistema, IP de gestión BMC)", False, "lista"),
                          ("usuario", "Usuario SSH", False, "text"), ("llave", "Llave privada SSH", True, "area"),
                          ("bmc_tipo", "Gestión fuera de banda", False, "bmc"), ("bmc_usuario", "Usuario BMC", False, "text"),
                          ("bmc_clave", "Clave BMC", True, "password"), ("sala", "Sala / rack", False, "text")]},
}
AMBITOS = {"on-premise": "On-premise", "nube": "Nube"}


def ambito(tipo):
    return PROVEEDORES.get(tipo, {}).get("ambito", "on-premise")


def iniciar():
    from .bd import con
    con().executescript(ESQUEMA)


def sembrar(cliente_id):
    """Las infraestructuras que ya existen en el stand."""
    for n, t, reg, det, cred, inv, ubi in (
            ("Centro de datos Santiago", "kvm", "Santiago, Chile",
             {"anfitrion": "192.168.122.1", "usuario": "aap-kvm", "red": "192.168.122.0/24", "hipervisor": "KVM / libvirt"},
             "FaceOps · anfitrión KVM (ssh)", "FaceOps · Infraestructura", "Centro de datos Santiago"),
            ("Cloud GCP · invoke-summit-cloud", "gcp", "southamerica-west1 (Santiago)",
             {"proyecto": "invoke-summit-cloud", "zona": "southamerica-west1-a", "red": "default", "etiqueta": "demo=summit-cloud"},
             "GCP · invoke-summit-cloud", "Nube GCP", "Cloud GCP")):
        if not q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente_id, n):
            ej("INSERT INTO infraestructuras(cliente_id, nombre, tipo, region, detalle, credencial, inventario, ubicacion, estado, creado) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", cliente_id, n, t, reg, json.dumps(det, ensure_ascii=False), cred, inv, ubi, "conectada", ahora())


def lista(cliente_id, eqs=None):
    out = q("SELECT * FROM infraestructuras WHERE cliente_id=? ORDER BY id", cliente_id)
    for x in out:
        x["detalle_d"] = j(x["detalle"], {})
        x["prov"] = PROVEEDORES.get(x["tipo"], {"nombre": x["tipo"]})
        x["ambito"] = ambito(x["tipo"])
        x["equipos"] = len([e for e in (eqs or []) if (e.get("ubicacion") or "") == x["ubicacion"]])
    return out


def _valores(p, f, exigir=True):
    v = {c: (f.get(c) or "").strip() for c, _, _, _ in p["campos"]}
    faltan = [et for c, et, sec, _ in p["campos"] if not v[c] and c not in OPCIONALES]
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


def _credencial(aap, org, tipo, nombre, v, existente=""):
    """Crea (o actualiza) la credencial cifrada de AAP con los secretos del formulario. Devuelve su nombre."""
    p = PROVEEDORES[tipo]
    ct = (aap.get("credential_types/", name=p["credencial_aap"]).get("results") or [None])[0]
    if not ct:
        raise ValueError(f"AAP no tiene el tipo de credencial «{p['credencial_aap']}»")
    if tipo in ("kvm", "fisico"):
        ins = {"username": v["usuario"], "ssh_key_data": v["llave"].strip() + "\n", "become_method": "sudo"}
    elif tipo == "vmware":
        ins = {"host": v["host"], "username": v["usuario"], "password": v["clave"]}
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


def _fuente(aap, inv, nombre, tipo, cred, v):
    p = PROVEEDORES[tipo]
    if not p["fuente"]:
        return
    sv = {"gce": {"zones": [v.get("zona")]} if v.get("zona") else {}, "ec2": {"regions": [v.get("region")]} if v.get("region") else {},
          "azure_rm": {}, "vmware": {}}[p["fuente"]]
    nom = f"{nombre} · {p['nombre']}"
    datos = {"name": nom, "inventory": inv["id"], "source": p["fuente"], "credential": cred["id"], "overwrite": True,
             "update_on_launch": False, "source_vars": json.dumps(sv)}
    ya = [x for x in aap.get("inventory_sources/", inventory=inv["id"]).get("results", []) if x.get("source") == p["fuente"]]
    if ya:
        aap.patch(f"inventory_sources/{ya[0]['id']}/", {"credential": cred["id"], "source_vars": json.dumps(sv)})
    else:
        aap.post("inventory_sources/", datos)


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
    fisicos = _equipos_fisicos(v["equipos"]) if tipo == "fisico" else []
    aap = AAP.de_cliente(cliente)
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    cred = _credencial(aap, org, tipo, nombre, v)
    bmc = _credencial_bmc(aap, org, nombre, v) if tipo == "fisico" else None
    inv = aap.post("inventories/", {"name": f"Infra · {nombre}", "organization": org, "description": f"Equipos de {nombre} ({p['nombre']})"})
    _fuente(aap, inv, nombre, tipo, cred, v)
    if tipo == "kvm":
        aap.post("hosts/", {"name": f"anfitrion-{nombre.lower().replace(' ', '-')[:30]}", "inventory": inv["id"],
                            "variables": json.dumps({"ansible_host": v["anfitrion"], "faceops_categoria": "servidor", "faceops_skills": ["kvm"]})})
    for e in fisicos:
        hv = {"ansible_host": e["ip"], "faceops_forma": "física", "faceops_categoria": "servidor"}
        if e["bmc"]:
            hv.update({"bmc_direccion": e["bmc"], "bmc_tipo": v.get("bmc_tipo") or "redfish"})
        aap.post("hosts/", {"name": e["nombre"], "inventory": inv["id"], "variables": json.dumps(hv)})
    publicos = {c: v[c] for c, _, sec, _ in p["campos"] if not sec and v[c] and c != "equipos"}
    if fisicos:
        publicos["equipos"] = len(fisicos)
    ej("INSERT INTO infraestructuras(cliente_id, nombre, tipo, region, detalle, credencial, inventario, ubicacion, estado, creado) VALUES (?,?,?,?,?,?,?,?,?,?)",
       cliente["id"], nombre, tipo, v.get("region") or v.get("zona") or v.get("sala") or "", json.dumps(publicos, ensure_ascii=False),
       cred["name"], inv["name"], nombre, "sin probar", ahora())
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
    publicos = dict(det, **{c: v[c] for c, _, sec, _ in p["campos"] if not sec and v[c] and c != "equipos"})
    ej("UPDATE infraestructuras SET credencial=?, detalle=?, estado='sin probar', mensaje='Credencial cargada en AAP: falta «Probar acceso»' WHERE id=?",
       cred["name"], json.dumps(publicos, ensure_ascii=False), iid)
    auditar(usuario["usuario"], cliente["id"], "conectar infraestructura", x["nombre"], {"credencial": cred["name"]})
    return x["nombre"]


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
        if not inv:
            raise ErrorAAP(f"No existe el inventario «{x['inventario']}» en AAP")
        fuentes = aap.get("inventory_sources/", inventory=inv["id"]).get("results") or []
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
        msg = f"{hosts} equipo(s) en su inventario de AAP" if st == "successful" else f"AAP: {st}"
    except ErrorAAP as e:
        msg = str(e)[:300]
    ej("UPDATE infraestructuras SET estado=?, verificado=?, mensaje=? WHERE id=?", estado, ahora(), msg, iid)
    auditar(usuario["usuario"], cliente["id"], "probar infraestructura", x["nombre"], {"estado": estado, "mensaje": msg})
    return estado, msg
