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

# proveedor → nombre, campos (var, etiqueta, secreto), tipo de credencial de AAP y fuente de inventario
PROVEEDORES = {
    "kvm": {"nombre": "Centro de datos (KVM)", "icono": "kvm", "fuente": None, "credencial_aap": "Machine",
            "campos": [("anfitrion", "Anfitrión KVM (IP o nombre)", False), ("usuario", "Usuario SSH", False),
                       ("llave", "Llave privada SSH", True), ("red", "Red de las VM (ej. 192.168.122.0/24)", False)]},
    "vmware": {"nombre": "VMware vSphere", "icono": "vmware", "fuente": "vmware", "credencial_aap": "VMware vCenter",
               "campos": [("host", "vCenter (FQDN o IP)", False), ("usuario", "Usuario", False), ("clave", "Clave", True),
                          ("datacenter", "Datacenter", False)]},
    "gcp": {"nombre": "Google Cloud", "icono": "gcp", "fuente": "gce", "credencial_aap": "Google Compute Engine",
            "campos": [("proyecto", "Proyecto", False), ("zona", "Zona (ej. southamerica-west1-a)", False),
                       ("cuenta", "Cuenta de servicio (JSON)", True), ("red", "Red VPC", False)]},
    "aws": {"nombre": "Amazon Web Services", "icono": "aws", "fuente": "ec2", "credencial_aap": "Amazon Web Services",
            "campos": [("access_key", "Access key ID", False), ("secret_key", "Secret access key", True),
                       ("region", "Región (ej. sa-east-1)", False), ("vpc", "VPC", False)]},
    "azure": {"nombre": "Microsoft Azure", "icono": "azure", "fuente": "azure_rm", "credencial_aap": "Microsoft Azure Resource Manager",
              "campos": [("suscripcion", "Suscripción", False), ("tenant", "Tenant ID", False), ("cliente", "Client ID", False),
                         ("secreto", "Client secret", True), ("region", "Región (ej. brazilsouth)", False)]},
}


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
        x["prov"] = PROVEEDORES.get(x["tipo"], {"nombre": x["tipo"], "icono": "otro"})
        x["equipos"] = len([e for e in (eqs or []) if (e.get("ubicacion") or "") == x["ubicacion"]])
    return out


def incorporar(usuario, cliente, f):
    """Crea la credencial (con los secretos del formulario) y el inventario en AAP; registra la infraestructura."""
    from .aap import AAP
    tipo, nombre = f.get("tipo", ""), (f.get("nombre") or "").strip()
    p = PROVEEDORES.get(tipo)
    if not p or not nombre:
        raise ValueError("Elige el proveedor y escribe un nombre")
    if q1("SELECT 1 FROM infraestructuras WHERE cliente_id=? AND nombre=?", cliente["id"], nombre):
        raise ValueError("Ya existe una infraestructura con ese nombre")
    v = {c: (f.get(c) or "").strip() for c, _, _ in p["campos"]}
    faltan = [et for c, et, sec in p["campos"] if not v[c] and c not in ("red", "vpc", "datacenter")]
    if faltan:
        raise ValueError("Faltan: " + ", ".join(faltan))
    aap = AAP.de_cliente(cliente)
    org = (aap.get("organizations/", name="FaceOps").get("results") or [{}])[0].get("id")
    ct = (aap.get("credential_types/", name=p["credencial_aap"]).get("results") or [None])[0]
    if not ct:
        raise ValueError(f"AAP no tiene el tipo de credencial «{p['credencial_aap']}»")
    if tipo == "kvm":
        ins = {"username": v["usuario"], "ssh_key_data": v["llave"]}
    elif tipo == "vmware":
        ins = {"host": v["host"], "username": v["usuario"], "password": v["clave"]}
    elif tipo == "gcp":
        try:
            sa = json.loads(v["cuenta"])
        except ValueError:
            raise ValueError("La cuenta de servicio debe ser el JSON que entrega GCP")
        ins = {"username": sa.get("client_email", ""), "project": v["proyecto"], "ssh_key_data": sa.get("private_key", "")}
    elif tipo == "aws":
        ins = {"username": v["access_key"], "password": v["secret_key"]}
    else:
        ins = {"subscription": v["suscripcion"], "tenant": v["tenant"], "client": v["cliente"], "secret": v["secreto"]}
    cred = aap.post("credentials/", {"name": f"Infra · {nombre}", "organization": org, "credential_type": ct["id"], "inputs": ins,
                                     "description": f"Acceso de FaceOps a {nombre} ({p['nombre']})"})
    inv = aap.post("inventories/", {"name": f"Infra · {nombre}", "organization": org, "description": f"Equipos de {nombre} ({p['nombre']})"})
    if p["fuente"]:
        sv = {"gce": {"zones": [v.get("zona")]} if v.get("zona") else {}, "ec2": {"regions": [v.get("region")]} if v.get("region") else {},
              "azure_rm": {}, "vmware": {}}[p["fuente"]]
        aap.post("inventory_sources/", {"name": f"{nombre} · {p['nombre']}", "inventory": inv["id"], "source": p["fuente"],
                                        "credential": cred["id"], "overwrite": True, "update_on_launch": False, "source_vars": json.dumps(sv)})
    elif tipo == "kvm":
        aap.post("hosts/", {"name": f"anfitrion-{nombre.lower().replace(' ', '-')[:30]}", "inventory": inv["id"],
                            "variables": json.dumps({"ansible_host": v["anfitrion"], "faceops_categoria": "servidor", "faceops_skills": ["kvm"]})})
    publicos = {c: v[c] for c, _, sec in p["campos"] if not sec}
    ej("INSERT INTO infraestructuras(cliente_id, nombre, tipo, region, detalle, credencial, inventario, ubicacion, estado, creado) VALUES (?,?,?,?,?,?,?,?,?,?)",
       cliente["id"], nombre, tipo, v.get("region") or v.get("zona") or "", json.dumps(publicos, ensure_ascii=False),
       cred["name"], inv["name"], nombre, "sin probar", ahora())
    # sus equipos entran al inventario de FaceOps con esta infraestructura como ubicación
    cl = j(cliente["clasificacion"], {})
    cl[inv["name"]] = {"proveedor": tipo, "ubicacion": nombre, "ambiente": "produccion", "forma": "virtual"}
    ej("UPDATE clientes SET clasificacion=? WHERE id=?", json.dumps(cl, ensure_ascii=False), cliente["id"])
    auditar(usuario["usuario"], cliente["id"], "incorporar infraestructura", nombre, {"tipo": tipo, "credencial": cred["name"], "inventario": inv["name"]})
    return nombre


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
