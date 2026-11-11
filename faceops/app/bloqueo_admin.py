"""Bloqueo de acceso decidido en FaceAdmin (consola global de Invoke), p. ej. por facturas impagas. Cada instancia pregunta a
FaceAdmin con su token propio (FACEOPS_ADMIN_URL, FACEOPS_ADMIN_TOKEN). Si FaceAdmin no responde, rige lo último que se supo."""
import json
import os
import time

import requests

from .bd import ajuste

_c = {"t": 0, "v": None}


def estado():
    """{'bloqueado': bool, 'motivo': str, 'aviso': str, 'posible_bloqueo': str} o {} si la instancia no está conectada a FaceAdmin."""
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok:
        return {}
    if _c["v"] is not None and time.time() - _c["t"] < 15:
        return _c["v"]
    v = None
    try:
        r = requests.get(url + "/api/instancia/estado", headers={"X-FaceAdmin-Token": tok}, timeout=4, verify=False)
        if r.ok:
            v = r.json()
            try:
                ajuste("faceadmin_estado", json.dumps(v))
            except Exception:  # noqa: BLE001  (p. ej. disco lleno): no botar la consola por no poder guardar el estado
                pass
    except (requests.RequestException, ValueError):
        v = None
    if v is None:
        try:
            v = json.loads(ajuste("faceadmin_estado") or "{}")
        except ValueError:
            v = {}
    _c.update(t=time.time(), v=v)
    if v and v.get("pedidos"):
        _atender_pedidos(url, tok, v["pedidos"])
    if v and "sincronizar" in v:   # fase de pruebas: la marca le dice al temporizador de la máquina que puede actualizar
        try:
            marca = os.path.join(os.path.dirname(os.environ.get("FACEOPS_BD", "/var/lib/faceops/faceops.db")), "sincronizar")
            if v["sincronizar"] and not os.path.exists(marca):
                open(marca, "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
            elif not v["sincronizar"] and os.path.exists(marca):
                os.remove(marca)
        except OSError:
            pass
    return v


_hechos = set()


def _atender_pedidos(url, tok, pedidos):
    """Pedidos de FaceAdmin que llegan en la respuesta del latido (FaceAdmin nunca entra a FaceOps: FaceOps los toma y responde hacia afuera).
    acceso_admin: un enlace de un solo uso para que el técnico a cargo (cluster-admin) defina su clave; FaceAdmin se lo envía por correo."""
    from .bd import q1
    for p in pedidos:
        if p.get("id") in _hechos or p.get("tipo") not in ("acceso_admin", "datos_demo"):
            continue
        _hechos.add(p.get("id"))
        if p.get("tipo") == "datos_demo":
            try:
                from . import demo_datos
                c = q1("SELECT id FROM clientes ORDER BY id LIMIT 1")
                n = demo_datos.quitar(c["id"]) if p.get("accion") == "quitar" else demo_datos.cargar(c["id"])
                res = {"ok": True, "detalle": f"{p.get('accion') or 'cargar'}: {n} registros"}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "motivo": f"{type(e).__name__}: {str(e)[:160]}"}
            try:
                requests.post(f"{url}/api/instancia/pedido/{p['id']}", json=res, headers={"X-FaceAdmin-Token": tok}, timeout=10, verify=False)
            except requests.RequestException:
                _hechos.discard(p.get("id"))
            continue
        res = {"ok": False, "motivo": "usuario desconocido"}
        try:
            usr = (p.get("usuario") or "").strip().lower()
            if usr and usr == (os.environ.get("FACEOPS_CONSULTOR_USUARIO") or "").strip().lower() and q1("SELECT id FROM usuarios WHERE usuario=?", usr):
                from .activacion import nuevo
                res = {"ok": True, "ruta": "/definir-clave/" + nuevo(usr, 72)}
            elif usr:
                res = {"ok": False, "motivo": "solo el técnico a cargo de la instancia recibe el acceso admin"}
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "motivo": type(e).__name__}
        try:
            requests.post(f"{url}/api/instancia/pedido/{p['id']}", json=res, headers={"X-FaceAdmin-Token": tok}, timeout=10, verify=False)
        except requests.RequestException:
            _hechos.discard(p.get("id"))


_u = {"t": 0.0, "corriendo": False}


def reportar_uso(cada=300):
    """Cada 10 minutos, en segundo plano: informa a FaceAdmin los nodos que gestiona el AAP de esta instancia (suscripción Red Hat)
    y las automatizaciones ejecutadas con sus créditos. Cada ejecución lleva su id: FaceAdmin la registra una sola vez."""
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok or _u["corriendo"] or time.time() - _u["t"] < cada:
        return
    _u.update(t=time.time(), corriendo=True)
    import threading
    threading.Thread(target=_reportar, args=(url, tok), daemon=True).start()


def _nodos():
    """Nodos gestionados según la licencia de AAP: instancias automatizadas (o, si no informa, hosts actuales)."""
    from .aap import AAP, ErrorAAP
    from .bd import q1
    c = q1("SELECT * FROM clientes WHERE COALESCE(aap_url,'')<>'' AND COALESCE(aap_token,'')<>'' ORDER BY id LIMIT 1")
    if not c:
        return None
    try:
        cli = AAP.de_cliente(c)
        lic = cli.licencia()
        if lic.get("comunitario"):   # AWX no tiene licencia: se informan los hosts del inventario
            n = int(cli.get("hosts/", page_size=1).get("count") or 0)
            return {"nodos": n, "automatizados": n, "fuente": "AWX comunitario · sin suscripción (hosts del inventario)"}
    except ErrorAAP:
        return None
    usados = lic.get("automated_instances")
    if usados is None:
        usados = lic.get("current_instances")
    return {"nodos": int(usados or 0), "automatizados": int(lic.get("automated_instances") or 0),
            "fuente": f"AAP · licencia {lic.get('instance_count', '?')} nodos"}


def _reportar(url, tok):
    from .bd import ajuste, q
    try:
        desde = int(ajuste("faceadmin_uso_ultimo") or 0)
        movs = q("SELECT id, fecha, creditos, detalle FROM creditos_mov WHERE tipo='ejecucion' AND id>? ORDER BY id LIMIT 500", desde)
        datos = {"consumo": [{"id": f"mov-{m['id']}", "fecha": m["fecha"], "creditos": -float(m["creditos"] or 0), "detalle": m["detalle"]}
                             for m in movs if float(m["creditos"] or 0) < 0]}
        n = _nodos()
        if n:
            datos.update(n)
        try:   # versión de esta instancia: FaceAdmin no se conecta a FaceOps, la conoce por lo que FaceOps le informa
            from . import version_info
            vi = version_info()
            v = {k: vi.get(k) for k in ("texto", "linea", "build", "commit", "fecha", "rama", "instancia", "rol")}
            v["instancia_id"] = os.environ.get("FACEOPS_INSTANCIA_ID", "")
            datos["version"] = v
        except (OSError, ValueError):
            pass
        r = requests.post(url + "/api/instancia/uso", json=datos, headers={"X-FaceAdmin-Token": tok}, timeout=10, verify=False)
        if r.ok and movs:
            ajuste("faceadmin_uso_ultimo", str(movs[-1]["id"]))
    except (requests.RequestException, ValueError):
        pass
    finally:
        _u["corriendo"] = False


def aprobar_activacion(usuario):
    """El cliente (cluster-admin de su FaceOps) aprueba la activación de su contrato en FaceAdmin."""
    url, tok = os.environ.get("FACEOPS_ADMIN_URL", "").rstrip("/"), os.environ.get("FACEOPS_ADMIN_TOKEN", "")
    if not url or not tok:
        raise ValueError("Esta instancia no está enlazada a FaceAdmin")
    try:
        r = requests.post(url + "/api/instancia/activar", json={"usuario": usuario}, headers={"X-FaceAdmin-Token": tok}, timeout=10, verify=False)
        d = r.json()
    except (requests.RequestException, ValueError) as e:
        raise ValueError("FaceAdmin no respondió; intenta de nuevo en unos minutos") from e
    if not r.ok:
        raise ValueError(d.get("error") or "No se pudo activar")
    _c.update(t=0)   # se vuelve a preguntar el estado en la próxima página
    return d


_lat = {"iniciado": False}


def iniciar_latido():
    """Latido hacia FaceAdmin en segundo plano, aunque nadie use FaceOps: cada minuto pregunta su estado (bloqueo, contrato) y
    cada 5 minutos informa versión, nodos y consumo. Solo sale de FaceOps hacia FaceAdmin; FaceAdmin nunca entra a FaceOps."""
    if _lat["iniciado"] or not os.environ.get("FACEOPS_ADMIN_URL") or not os.environ.get("FACEOPS_ADMIN_TOKEN"):
        return
    _lat["iniciado"] = True
    import threading

    def ciclo():
        while True:
            try:
                _c.update(t=0)
                estado()
                reportar_uso()
            except Exception:  # noqa: BLE001  el latido nunca debe botar FaceOps
                pass
            time.sleep(60)
    threading.Thread(target=ciclo, daemon=True, name="latido-faceadmin").start()
