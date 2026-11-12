"""Sistema operativo de una máquina según la fuente que lo sabe de verdad. Orden:
1.º facts de Ansible (ansible_distribution / ansible_os_name en Windows), 2.º lo que informa la nube (GCP: licencia o imagen
del disco de arranque y guestOsFeatures; Azure: imageReference / osType; AWS: platform_details / image), 3.º variables
declaradas (faceops_so / so). Si nada lo dice, queda vacío y la ficha muestra «Sin detectar» (nunca «Servidor»)."""
import re

SIN_DETECTAR = "Sin detectar"

# fragmento de la licencia o imagen de GCP → (sistema, familia). La versión sale del mismo nombre.
_GCP = [
    (r"windows-server-(\d{4})(?:-r2)?-dc", "Windows Server {v} Datacenter", "Windows"),
    (r"windows-server-(\d{4})", "Windows Server {v}", "Windows"),
    (r"windows-(\d+)", "Windows {v}", "Windows"),
    (r"rhel-(\d+)(?:-(\d+))?", "Red Hat Enterprise Linux {v}", "RedHat"),
    (r"rocky-linux-(\d+)", "Rocky Linux {v}", "RedHat"),
    (r"almalinux-(\d+)", "AlmaLinux {v}", "RedHat"),
    (r"centos-stream-(\d+)", "CentOS Stream {v}", "RedHat"),
    (r"centos-(\d+)", "CentOS {v}", "RedHat"),
    (r"oracle-linux-(\d+)", "Oracle Linux {v}", "RedHat"),
    (r"ubuntu-(?:pro-)?(\d{2})(\d{2})", "Ubuntu {v}", "Debian"),
    (r"debian-(\d+)", "Debian {v}", "Debian"),
    (r"sles-(?:sap-)?(\d+)", "SUSE Linux Enterprise {v}", "Suse"),
    (r"fedora-coreos", "Fedora CoreOS", "RedHat"),
    (r"rhcos", "Red Hat Enterprise Linux CoreOS", "RedHat"),
    (r"cos-", "Container-Optimized OS", "Linux"),
]


def _de_nombre(*txts):
    """El primer patrón (en orden de _GCP) que calce con alguno de los nombres: el sistema manda sobre el producto (SQL Server)."""
    ts = [(t or "").rsplit("/", 1)[-1].lower() for t in txts if t]
    for patron, nom, fam in _GCP:
        for t in ts:
            m = re.search(patron, t)
            if not m:
                continue
            g = [x for x in m.groups() if x]
            v = ".".join(g)
            if "{v}" in nom and not v:
                nom = nom.replace(" {v}", "")
            return nom.format(v=v).strip(), fam, (g[0] if g else "")
    return None


def so_de_gcp(hv):
    """(sistema, familia, versión) de una VM de GCP: licencias o imagen del disco de arranque; guestOsFeatures como último recurso."""
    discos = [d for d in (hv.get("disks") or []) if isinstance(d, dict)]
    discos.sort(key=lambda d: not d.get("boot"))
    for d in discos:
        r = _de_nombre(*(list(d.get("licenses") or []) + [d.get("sourceImage") or ""]))
        if r:
            return r
    r = _de_nombre(hv.get("sourceImage") or "")
    if r:
        return r
    feats = {(x.get("type") or "").upper() for d in discos for x in (d.get("guestOsFeatures") or []) if isinstance(x, dict)}
    if "WINDOWS" in feats:
        return "Windows", "Windows", ""
    return None


def so_de_azure(hv):
    img = hv.get("image") or ((hv.get("storageProfile") or {}).get("imageReference") if isinstance(hv.get("storageProfile"), dict) else None) or {}
    if isinstance(img, dict) and (img.get("offer") or img.get("sku")):
        off, sku = str(img.get("offer") or ""), str(img.get("sku") or "")
        if "windows" in (off + sku).lower():
            m = re.search(r"(\d{4})", sku)
            return (f"Windows Server {m.group(1)}" + (" Datacenter" if "datacenter" in sku.lower() else "")) if m else "Windows Server", "Windows", (m.group(1) if m else "")
        if off.upper().startswith("RHEL"):
            v = sku.split(".")[0].split("_")[0]
            return f"Red Hat Enterprise Linux {sku}".strip(), "RedHat", v
        if "ubuntu" in off.lower():
            m = re.search(r"(\d{2})[._]?(\d{2})", off + " " + sku)
            return (f"Ubuntu {m.group(1)}.{m.group(2)}" if m else "Ubuntu"), "Debian", (m.group(1) if m else "")
    t = str(hv.get("os_type") or hv.get("osType") or ((hv.get("os_profile") or {}).get("system") if isinstance(hv.get("os_profile"), dict) else "") or "")
    if t.lower() == "windows":
        return "Windows", "Windows", ""
    if t.lower() == "linux":
        return "Linux", "Linux", ""
    return None


def so_de_aws(hv):
    p = str(hv.get("platform_details") or hv.get("platform") or "")
    if "windows" in p.lower():
        return "Windows Server", "Windows", ""
    if "red hat" in p.lower():
        return "Red Hat Enterprise Linux", "RedHat", ""
    if "suse" in p.lower():
        return "SUSE Linux Enterprise", "Suse", ""
    if "ubuntu" in p.lower():
        return "Ubuntu", "Debian", ""
    return None


def so_de_nube(hv):
    """Lo que dice el proveedor de la nube sobre el sistema de la VM, o None."""
    hv = hv or {}
    if hv.get("disks") or hv.get("sourceImage") or str(hv.get("selfLink") or "").startswith("https://www.googleapis.com/compute"):
        r = so_de_gcp(hv)
        if r:
            return r
    for fn in (so_de_azure, so_de_aws):
        r = fn(hv)
        if r:
            return r
    return None


def so_de_facts(f):
    """(sistema, familia, versión) de los facts de Ansible. En Windows ansible_distribution ya es «Microsoft Windows Server 2022
    Datacenter»; si falta, ansible_os_name. Se quita el «Microsoft » inicial."""
    f = f or {}
    fam = f.get("ansible_os_family", "") or ""
    dist = str(f.get("ansible_distribution") or "").strip()
    ver = str(f.get("ansible_distribution_version") or "").strip()
    if fam == "Windows" or dist.lower().startswith("microsoft") or "windows" in str(f.get("ansible_os_name") or "").lower():
        nom = dist or str(f.get("ansible_os_name") or "")
        nom = re.sub(r"^Microsoft\s+", "", nom).strip()
        m = re.search(r"\b(20\d\d)\b", nom)
        return nom, "Windows", (m.group(1) if m else str(f.get("ansible_distribution_major_version") or ""))
    if dist:
        return f"{dist} {ver}".strip(), fam, str(f.get("ansible_distribution_major_version") or "")
    return None


def so_de_pedido(cod):
    """Sistema pedido al crear la máquina en FaceOps («Habilitar servidor»: windows2022, rhel9, ubuntu2404…). Solo se usa
    mientras no haya facts ni dato de la nube: es lo que se instaló, no lo que se midió."""
    c = (cod or "").lower().strip()
    m = re.match(r"windows(\d{4})", c)
    if m:
        return f"Windows Server {m.group(1)}", "Windows", m.group(1)
    for pre, nom, fam in (("rhel", "Red Hat Enterprise Linux", "RedHat"), ("rocky", "Rocky Linux", "RedHat"), ("alma", "AlmaLinux", "RedHat"),
                          ("centos", "CentOS", "RedHat"), ("debian", "Debian", "Debian"), ("sles", "SUSE Linux Enterprise", "Suse")):
        m = re.match(pre + r"(\d+)", c)
        if m:
            return f"{nom} {m.group(1)}", fam, m.group(1)
    m = re.match(r"ubuntu(\d{2})(\d{2})", c)
    if m:
        return f"Ubuntu {m.group(1)}.{m.group(2)}", "Debian", m.group(1)
    return None


def so_declarado(hv):
    """faceops_so / so (texto libre) o el sistema pedido al crear la máquina."""
    hv = hv or {}
    if hv.get("faceops_so") or hv.get("so"):
        t = str(hv.get("faceops_so") or hv.get("so"))
        return t, ("Windows" if "windows" in t.lower() else ""), ""
    return so_de_pedido(hv.get("faceops_so_pedido"))
