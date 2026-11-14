"""Ficha de la máquina · piezas propias: acceso directo SSH/RDP (sin credenciales), segmentos de negocio de la máquina
(con su edición) y estado de vinculación y medición agentless.

Principio: toda máquina se inventaría y mide SIN agente (Ansible por SSH o WinRM). El agente de monitoreo
(node_exporter / windows_exporter) es opcional y solo agrega métricas continuas. Una máquina que no se creó desde FaceOps
(p. ej. descubierta en la nube) se vincula con «Incorporar a FaceOps», que solo configura el acceso SSH o WinRM."""
import json
import socket
import time

from flask import Response, abort, g, redirect, request, url_for

from . import requiere_login
from .bd import auditar, ej, j, q, q1
from .vistas import _escritura, bp, equipos_visibles

INCORPORAR = "FaceOps · Incorporar máquinas"   # con inc_maquina: solo esta máquina (configura SSH o WinRM; no instala agentes)
_ALCANZA = {}


def _probar(host, puerto):
    try:
        with socket.create_connection((host, puerto), timeout=1):
            return True
    except OSError:
        return False


def _alcanza(host, puerto):
    """¿Responde el puerto desde FaceOps? Nunca espera: responde lo último probado (60 s) y la prueba TCP (≤ 1 s) corre en
    segundo plano. None: todavía no se sabe (la ficha muestra «comprobando…» y se rehidrata sola)."""
    from . import sin_espera
    return sin_espera.valor(("tcp", host, puerto), lambda: _probar(host, puerto), ttl=60)


def _windows(e):
    return "windows" in (e.get("so") or "").lower() or (e.get("so_familia") or "").lower() == "windows"


@bp.app_template_global("acceso_directo")
def acceso_directo(e):
    """Botón para entrar a la máquina según su sistema: RDP (archivo .rdp con host y puerto) en Windows, SSH (comando a
    copiar) en Linux. Nunca lleva claves. Deshabilitado con su motivo si no hay IP alcanzable o no se sabe el sistema."""
    if (e.get("categoria") or "servidor") not in ("servidor", "estación de trabajo"):
        return None
    win = _windows(e)
    linux = not win and any(x in (e.get("so") or "").lower() for x in ("linux", "red hat", "redhat", "rhel", "ubuntu", "debian", "rocky",
                                                                      "centos", "alma", "suse", "oracle", "fedora", "coreos"))
    proto = "rdp" if win else ("ssh" if linux else "")
    d = {"proto": proto, "t": "RDP" if win else "SSH", "host": e.get("ip") or "", "puerto": 3389 if win else 22, "ok": False, "motivo": ""}
    usuario = ""
    try:
        from .boveda import acceso
        usuario = ((acceso(e["nombre"], esperar=False) or {}).get("usuario") or "") if e.get("ip") else ""
        from . import boveda as bv_
        pend_bv = bool(e.get("ip")) and bv_.disponible() and bv_.responde() and f"maquinas/{e['nombre']}" not in bv_._cache
    except Exception:  # noqa: BLE001 - sin bóveda: el usuario se escribe a mano
        usuario, pend_bv = "", False
    d["usuario"] = usuario
    d["pendiente"] = pend_bv
    if not proto:
        d.update(t="SSH / RDP", motivo="Sistema operativo sin detectar: no se sabe si se entra por SSH o RDP")
    elif not e.get("ip"):
        d["motivo"] = "La máquina no tiene IP en el inventario"
    elif e.get("energia") in ("apagado", "suspendido"):
        d["motivo"] = "La máquina está apagada"
    else:
        al_ = _alcanza(e["ip"], d["puerto"])
        if al_ is None:
            d.update(motivo=f"comprobando {e['ip']}:{d['puerto']}…", pendiente=True)
        elif not al_:
            d["motivo"] = f"{e['ip']}:{d['puerto']} no responde desde FaceOps (firewall o servicio {'de escritorio remoto' if win else 'SSH'} detenido)"
        else:
            d["ok"] = True
    if proto == "ssh":
        d["cmd"] = f"ssh {usuario or '<usuario>'}@{d['host']}" + ("" if d["puerto"] == 22 else f" -p {d['puerto']}")
        d["url"] = f"ssh://{(usuario + '@') if usuario else ''}{d['host']}"
    elif proto == "rdp":
        d["url"] = url_for("consola.acceso_rdp", eid=e["id"])
    return d


@bp.route("/inventario/<int:eid>/acceso.rdp")
@requiere_login
def acceso_rdp(eid):
    """Archivo .rdp con host y puerto (y el usuario de servicio si está en la bóveda). Sin clave: la pide el cliente RDP."""
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e or not e.get("ip"):
        abort(404)
    d = acceso_directo(e) or {}
    lineas = [f"full address:s:{e['ip']}:3389", "prompt for credentials:i:1", "authentication level:i:2", "screen mode id:i:2"]
    if d.get("usuario"):
        lineas.append(f"username:s:{d['usuario']}")
    auditar(g.usuario["usuario"], g.cliente["id"], "descargar acceso RDP", e["nombre"], {"host": e["ip"]})
    return Response("\r\n".join(lineas) + "\r\n", mimetype="application/x-rdp",
                    headers={"Content-Disposition": f'attachment; filename="{e["nombre"]}.rdp"'})


# ------------------------------------------------------------------ segmentos de negocio de la máquina
def _clasif(cliente_id):
    return j((q1("SELECT clasificacion FROM clientes WHERE id=?", cliente_id) or {}).get("clasificacion"), {})


def asociados(cliente_id, nombre):
    """Segmentos asociados a mano desde la ficha (clasificación del cliente › _equipos › <máquina> › segmentos)."""
    return list((_clasif(cliente_id).get("_equipos", {}).get(nombre) or {}).get("segmentos") or [])


@bp.app_template_global("segmentos_ficha")
def segmentos_ficha(e):
    """Segmentos de negocio a los que pertenece la máquina, con su grupo, responsable y salud agregada. Solo datos reales:
    si un segmento no tiene responsable o salud, se muestra «—»."""
    from .duenos import de as dueno_de
    from .grupos_segmento import NOMBRE, grupo_de
    cid = g.cliente["id"]
    todos = {s["nombre"]: s for s in q("SELECT * FROM segmentos WHERE cliente_id=? ORDER BY orden, nombre", cid)}
    mios = list(dict.fromkeys(list(e.get("segmentos") or []) + list(j(e.get("segmentos_fijos"), [])) + asociados(cid, e["nombre"])))
    out = []
    eqs = hs = None
    for n in mios:
        s = todos.get(n)
        if not s:
            continue
        fi = j(s.get("ficha"), {})
        du = dueno_de(cid, "segmento", n) or dueno_de(cid, "segmento", s["id"])
        resp = (du or {}).get("nombre") or fi.get("responsable") or fi.get("dueno") or ""
        salud = None
        try:
            from .topologia import resumen_segmento
            from .vistas import hallazgos_visibles
            if eqs is None:
                eqs = equipos_visibles()
                hs = hallazgos_visibles(eqs)
            salud = resumen_segmento(g.cliente, eqs, hs, n, e.get("ambiente") or "").get("salud_pct")
        except Exception:  # noqa: BLE001 - sin salud calculable se muestra «—»
            salud = None
        out.append({"nombre": n, "grupo": NOMBRE.get(grupo_de(s), ""), "area": s.get("area") or "", "responsable": resp, "salud": salud,
                    "descripcion": s.get("descripcion") or "",
                    "url": url_for("consola.mapa", vista="aplicaciones", segmento=n, ambiente=e.get("ambiente") or None)})
    return {"lista": out, "todos": [{"nombre": n, "grupo": NOMBRE.get(grupo_de(s), ""), "sel": n in mios} for n, s in todos.items()]}


@bp.route("/inventario/<int:eid>/segmentos", methods=["POST"])
@requiere_login
def equipo_segmentos(eid):
    """Asocia la máquina a uno o más segmentos. Queda en la clasificación del cliente (la sincronización con AAP la respeta)."""
    _escritura()
    e = next((x for x in equipos_visibles() if x["id"] == eid), None)
    if not e:
        abort(404)
    validos = {s["nombre"] for s in q("SELECT nombre FROM segmentos WHERE cliente_id=?", g.cliente["id"])}
    sel = sorted({s for s in request.form.getlist("segmento") if s in validos})
    cl = _clasif(g.cliente["id"])
    eq = cl.setdefault("_equipos", {}).setdefault(e["nombre"], {})
    eq["segmentos"] = sel
    ej("UPDATE clientes SET clasificacion=? WHERE id=?", json.dumps(cl, ensure_ascii=False), g.cliente["id"])
    # los de AAP (faceops_segmento) se mantienen; los de la ficha se suman
    ya = [x for x in j(e.get("segmentos_fijos"), []) if x not in validos or x in sel]
    ej("UPDATE equipos SET segmentos_fijos=? WHERE id=?", json.dumps(sorted(set(ya) | set(sel)), ensure_ascii=False), eid)
    auditar(g.usuario["usuario"], g.cliente["id"], "segmentos de la máquina", e["nombre"], {"segmentos": sel})
    return redirect(url_for("consola.equipo", eid=eid) + "#segmentos-negocio")


# ------------------------------------------------------------------ vinculación y medición agentless
@bp.app_template_global("vinculo_maquina")
def vinculo_maquina(e, datos):
    """Estado de acceso y medición sin agente. FaceOps inventaría y mide por SSH o WinRM; sin vínculo no hay datos del SO."""
    win = _windows(e)
    facts = bool(e.get("ultima_recoleccion")) or bool((datos or {}).get("recolectado"))
    try:
        from .boveda import acceso
        cred = bool(acceso(e["nombre"]))
    except Exception:  # noqa: BLE001
        cred = False
    hab = (datos or {}).get("vinculo") or {}
    vinculada = facts or cred or bool(hab.get("ok"))
    from .mod_nubes import _accion_lista
    from .operacion import VINCULAR_ONPREM, infra_de_maquina
    from .vistas import energia_posible
    inf = infra_de_maquina(g.cliente["id"], e["nombre"])
    if not inf:
        habs = [x for x in q("SELECT * FROM infraestructuras WHERE cliente_id=? AND tipo='gcp'", g.cliente["id"])]
        inf = habs[0] if len(habs) == 1 and "gcp" in (e.get("ubicacion") or e.get("proveedor") or "").lower() else None
    onprem = not inf and energia_posible(e)   # VM del anfitrión KVM: la vincula «Vincular máquina on-premise» (la enciende si hace falta)
    accion = INCORPORAR if inf else (VINCULAR_ONPREM if onprem else "")
    a, motivo = _accion_lista(g.cliente["id"], accion) if accion else (None, "")
    if not accion:   # lo primero: si no aplica a esta máquina, se dice eso (no que falta certificarla)
        motivo = "Solo para máquinas de una nube conectada (GCP, Azure o AWS) o del anfitrión KVM: esta no es de ninguna"
    if inf and not motivo and e.get("energia") in ("apagado", "suspendido"):
        motivo = "La máquina está apagada: enciéndela para incorporarla"
    nube = bool(inf) or "gcp" in (e.get("proveedor") or "").lower() or "cloud" in (e.get("ubicacion") or "").lower()
    etiqueta = "Incorporar a FaceOps" if inf else f"Vincular por {'WinRM' if win else 'SSH'}"
    return {"vinculada": vinculada, "facts": facts, "credencial": cred, "protocolo": "WinRM" if win else "SSH", "nube": nube,
            "incorporar": a, "motivo_incorporar": motivo, "infra": (inf or {}).get("nombre", ""), "onprem": onprem, "etiqueta": etiqueta,
            "aviso": "" if facts else (f"Sin vínculo {'WinRM' if win else 'SSH'}: FaceOps no puede inventariar ni medir el sistema operativo. "
                                       + ("Vincúlala" if onprem else "Incorpórala") + " para inventariarla y medirla sin agente.")}


@bp.route("/inventario/cluster-vmware/<nombre>")
@requiere_login
def cluster_vmware(nombre):
    """Vista del clúster de VMware (segmento de negocio de los nodos ESXi): sus hosts físicos y las VMs del inventario que
    corren en ellos (si las hay)."""
    from flask import render_template
    from .hardware_fisico import cluster_vmware as cl_de, es_fisico
    eqs = equipos_visibles()
    hosts = []
    for x in eqs:
        if (x.get("estado") or "") == "retirado" or not es_fisico(x):
            continue
        c = cl_de(x)
        if c and c["nombre"] == nombre:
            hosts.append({"e": x, "cl": c})
    if not hosts:
        abort(404)
    nombres = {h["e"]["nombre"] for h in hosts}
    vms = [x for x in eqs if (x.get("forma") or "") == "virtual" and (x.get("estado") or "") != "retirado"
           and (j(x.get("datos"), {}) or {}).get("esxi_host") in nombres]
    return render_template("cluster_vmware.html", nombre=nombre, cl=hosts[0]["cl"], hosts=hosts, vms=vms)
