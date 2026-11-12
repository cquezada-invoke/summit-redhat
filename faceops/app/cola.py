"""Cola de acciones de FaceOps: crear o aprobar un ticket nunca llama a AAP en la petición web.

La petición solo escribe en la base de FaceOps y encola un trabajo («lanzar la acción del ticket X» o «responder su aprobación
en AAP»). Un proceso propio (``python -m app.cola``, lo levanta y vigila FaceOps; un candado de archivo asegura uno solo)
consume la cola:

- Antes de lanzar consulta la salud de AAP (ping) y su capacidad (grupos de instancias y trabajos pendientes). Si AAP no está
  disponible o no tiene capacidad, el trabajo queda «pendiente» con espera creciente (sin gastar intentos) y el ticket muestra
  «En cola (posición N) · AAP no disponible / sin capacidad».
- Al lanzar guarda el job_id en el ticket (el sincronizador copia luego su estado y resultados) y el trabajo pasa a «lanzado».
- Un error transitorio al lanzar se reintenta con espera creciente hasta MAX_INTENTOS; luego pasa a «fallido» con su motivo,
  igual que un error definitivo de AAP (que falla al primer intento, como antes).

Reglas que se mantienen: FaceOps nunca toca máquinas directo (todo va por AAP con ticket y aprobación) y las acciones
bloqueadas por su plan de prueba se rechazan al solicitar (no llegan a la cola). Las claves de un ticket viajan cifradas en la
cola (solo hasta lanzarlo) y nunca se escriben en claro ni en el log."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

from .bd import ahora, ajuste, auditar, con, ej, q, q1

ESPERA_CICLO = 1.0          # s entre revisiones de la cola cuando no hay trabajo
MAX_INTENTOS = 6            # lanzamientos fallidos (error transitorio) antes de pasar a «fallido»
MAX_ESPERA_AAP = 12 * 3600  # s: un trabajo que esperó a AAP más que esto pasa a «fallido»
MAX_PENDIENTES_AAP = int(os.environ.get("FACEOPS_COLA_MAX_PENDIENTES", "15"))   # trabajos «pending» en AAP = sin capacidad
POR_CICLO = 4               # lanzamientos por cliente antes de volver a mirar la capacidad
TOMADO_VENCE = 600          # s: un trabajo «en curso» de un proceso que murió vuelve a «pendiente»
ACTIVOS = ("pendiente", "en_curso")
ETIQUETA = {"pendiente": "En cola", "en_curso": "Lanzando", "lanzado": "Lanzado", "fallido": "Fallido", "cancelado": "Cancelado"}
_tabla_ok = {"v": False}
_latido = {"v": 0.0}


def _tabla():
    if _tabla_ok["v"]:
        return
    con().executescript("""
CREATE TABLE IF NOT EXISTS cola_acciones (
  id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, ticket_id INTEGER NOT NULL, tipo TEXT NOT NULL DEFAULT 'lanzar',
  datos TEXT DEFAULT '{}', secretos TEXT DEFAULT '', estado TEXT NOT NULL DEFAULT 'pendiente',
  intentos INTEGER DEFAULT 0, esperas INTEGER DEFAULT 0, proximo REAL DEFAULT 0, motivo TEXT DEFAULT '',
  job_id INTEGER, creado TEXT, creado_ts REAL, actualizado TEXT, tomado REAL, tomado_por TEXT DEFAULT '', usuario TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS cola_acciones_estado ON cola_acciones(estado, proximo);
CREATE INDEX IF NOT EXISTS cola_acciones_ticket ON cola_acciones(ticket_id);
""")
    con().commit()
    _tabla_ok["v"] = True


# ── claves del ticket: cifradas mientras esperan en la cola ───────────────────────────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    from . import bd
    clave = os.environ.get("FACEOPS_CLAVE_SESION")
    if not clave:
        ruta = os.path.join(os.path.dirname(bd.RUTA), "clave-sesion")
        clave = open(ruta).read().strip()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("faceops-cola:" + clave).encode()).digest()))


def _cifrar(secretos):
    return _fernet().encrypt(json.dumps(secretos).encode()).decode() if secretos else ""


def _descifrar(texto):
    return json.loads(_fernet().decrypt(texto.encode())) if texto else None


# ── encolar (lo usan las peticiones web: solo escriben en la base) ───────────────────────────────────────────────────────
def encolar(ticket_id, tipo="lanzar", datos=None, secretos=None, usuario=""):
    """Encola un trabajo para el ticket. Un ticket tiene a lo más un «lanzar» activo (no se duplica)."""
    _tabla()
    t = q1("SELECT id, cliente_id FROM tickets WHERE id=?", ticket_id)
    if not t:
        raise ValueError(f"No existe el ticket {ticket_id}")
    if tipo == "lanzar":
        ya = q1("SELECT id FROM cola_acciones WHERE ticket_id=? AND tipo='lanzar' AND estado IN ('pendiente','en_curso')", ticket_id)
        if ya:
            return ya["id"]
    if secretos:
        datos = dict(datos or {}, tenia_secretos=True)
    return ej("INSERT INTO cola_acciones(cliente_id, ticket_id, tipo, datos, secretos, estado, proximo, creado, creado_ts, actualizado, usuario) "
              "VALUES (?,?,?,?,?,'pendiente',0,?,?,?,?)", t["cliente_id"], ticket_id, tipo, json.dumps(datos or {}, ensure_ascii=False),
              _cifrar(secretos), ahora(), time.time(), ahora(), usuario or "")


def activo(ticket_id):
    """¿El ticket tiene un trabajo de lanzamiento esperando en la cola? (el vigía no lo da por pegado)."""
    _tabla()
    return bool(q1("SELECT 1 x FROM cola_acciones WHERE ticket_id=? AND estado IN ('pendiente','en_curso')", ticket_id))


def aprobacion_en_cola(ticket_id, aprobacion_id):
    """¿Esa aprobación de AAP ya se respondió en FaceOps y la respuesta espera en la cola? (el ticket no vuelve a «espera aprobación»)."""
    _tabla()
    for c in q("SELECT datos FROM cola_acciones WHERE ticket_id=? AND tipo='aprobar' AND estado IN ('pendiente','en_curso')", ticket_id):
        if json.loads(c["datos"] or "{}").get("aprobacion") == aprobacion_id:
            return True
    return False


def _estado_aap(cliente_id):
    return json.loads(ajuste(f"cola_aap_{cliente_id}") or "{}")


def info_ticket(ticket_id):
    """Estado del ticket en la cola para la ficha y los botones: {estado, texto, posicion, motivo, job_id, ...} o None."""
    _tabla()
    c = q1("SELECT * FROM cola_acciones WHERE ticket_id=? AND tipo='lanzar' ORDER BY id DESC LIMIT 1", ticket_id)
    if not c:
        return None
    r = {"id": c["id"], "estado": c["estado"], "intentos": c["intentos"], "job_id": c["job_id"], "motivo": c["motivo"] or ""}
    if c["estado"] in ACTIVOS:
        r["posicion"] = q1("SELECT COUNT(*) n FROM cola_acciones WHERE cliente_id=? AND estado IN ('pendiente','en_curso') AND id<=?",
                           c["cliente_id"], c["id"])["n"]
        ea = _estado_aap(c["cliente_id"])
        por_que = c["motivo"] or ("" if ea.get("ok", True) else ea.get("motivo", ""))
        r["texto"] = (f"En cola (posición {r['posicion']})" if c["estado"] == "pendiente" else "Lanzando en AAP") + (f" · {por_que}" if por_que else "")
    elif c["estado"] == "lanzado":
        r["texto"] = f"Lanzado · job #{c['job_id']}" if c["job_id"] else "Lanzado"
    elif c["estado"] == "fallido":
        r["texto"] = "No se pudo lanzar · " + (c["motivo"] or "")
    else:
        r["texto"] = ETIQUETA.get(c["estado"], c["estado"])
    return r


def listar(cliente_id, limite=200):
    _tabla()
    filas = q("SELECT c.*, t.numero, t.estado AS t_estado, a.nombre AS accion FROM cola_acciones c JOIN tickets t ON t.id=c.ticket_id "
              "LEFT JOIN automatizaciones a ON a.id=t.automatizacion_id WHERE c.cliente_id=? AND (c.estado IN ('pendiente','en_curso') "
              "OR c.actualizado >= ?) ORDER BY CASE WHEN c.estado IN ('pendiente','en_curso') THEN 0 ELSE 1 END, c.id DESC LIMIT ?",
              cliente_id, time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 7 * 86400)), limite)
    pos = 0
    for f in sorted((x for x in filas if x["estado"] in ACTIVOS), key=lambda x: x["id"]):
        pos += 1
        f["posicion"] = pos
    for f in filas:
        f.pop("secretos", None)
        f["datos"] = json.loads(f["datos"] or "{}")
        f["etiqueta"] = ETIQUETA.get(f["estado"], f["estado"])
    return filas


def reintentar(cliente_id, cid, usuario):
    _tabla()
    c = q1("SELECT * FROM cola_acciones WHERE id=? AND cliente_id=?", cid, cliente_id)
    if not c or c["estado"] not in ("fallido", "pendiente"):
        raise ValueError("Solo se reintenta un trabajo fallido o en espera")
    t = q1("SELECT * FROM tickets WHERE id=?", c["ticket_id"])
    if c["tipo"] == "lanzar":
        if t.get("aap_job_id"):
            raise ValueError(f"{t['numero']} ya se lanzó en AAP (job #{t['aap_job_id']})")
        if c["estado"] == "fallido" and c["secretos"] == "" and json.loads(c["datos"] or "{}").get("tenia_secretos"):
            raise ValueError(f"{t['numero']} llevaba claves que ya no se guardan: vuelve a solicitarlo")
        ej("UPDATE tickets SET estado='en_cola', fin=NULL, resultado='' WHERE id=? AND estado IN ('fallido','en_cola')", t["id"])
    ej("UPDATE cola_acciones SET estado='pendiente', intentos=0, esperas=0, proximo=0, motivo='', actualizado=? WHERE id=?", ahora(), cid)
    auditar(usuario, cliente_id, "reintentar en la cola", t["numero"], {"cola": cid, "tipo": c["tipo"]})


def cancelar(cliente_id, cid, usuario):
    _tabla()
    c = q1("SELECT * FROM cola_acciones WHERE id=? AND cliente_id=?", cid, cliente_id)
    if not c or c["estado"] not in ("pendiente", "fallido"):
        raise ValueError("Solo se cancela un trabajo en espera o fallido (uno que se está lanzando ya no se puede detener aquí)")
    t = q1("SELECT * FROM tickets WHERE id=?", c["ticket_id"])
    ej("UPDATE cola_acciones SET estado='cancelado', secretos='', motivo=?, actualizado=? WHERE id=?", f"Cancelado por {usuario}", ahora(), cid)
    if c["tipo"] == "lanzar" and t["estado"] in ("en_cola", "fallido") and not t.get("aap_job_id"):
        ej("UPDATE tickets SET estado='cancelado', fin=?, resultado=? WHERE id=?", ahora(),
           f"Cancelado en la cola de acciones por {usuario}: no se lanzó en AAP", t["id"])
        try:
            ej("UPDATE ticket_etapas SET estado='cancelada', fin=? WHERE ticket_id=? AND estado='pendiente'", ahora(), t["id"])
        except Exception:  # noqa: BLE001 - sin etapas registradas
            pass
    auditar(usuario, cliente_id, "cancelar en la cola", t["numero"], {"cola": cid, "tipo": c["tipo"]})


# ── el proceso que consume la cola ───────────────────────────────────────────────────────────────────────────────────────
def _salud_y_capacidad(aap):
    """(ok, motivo, detalle): ping a AAP y capacidad (grupos de instancias con capacidad libre, trabajos pendientes)."""
    from . import aap as aap_mod
    simulado = float(ajuste("cola_prueba_aap_caido_hasta") or 0)   # plan de prueba: AAP «caído» por unos minutos (vence solo)
    if simulado > time.time():
        return False, "AAP no disponible (simulado por el plan de prueba)", {}
    aap_mod._ping(aap.web)   # en este proceso el ping es directo (el vigía de la consola web no corre aquí)
    if not aap_mod.disponible(aap.web):
        return False, "AAP no disponible", {}
    det = {}
    try:
        grupos = [x for x in aap.get("instance_groups/", page_size=50).get("results", []) if not x.get("is_container_group")]
        conc = [x for x in grupos if (x.get("capacity") or 0) > 0]
        det["capacidad"] = sum(max(0, (x.get("capacity") or 0) - (x.get("consumed_capacity") or 0)) for x in conc)
        det["grupos"] = len(conc)
        pend = aap.get("jobs/", status="pending", page_size=1).get("count", 0)
        det["pendientes"] = pend
        if grupos and not conc:
            return False, "AAP sin capacidad (sin nodos de ejecución activos)", det
        if conc and all((x.get("percent_capacity_remaining") or 0) <= 0 for x in conc) and pend > 0:
            return False, "AAP sin capacidad", det
        if pend >= MAX_PENDIENTES_AAP and det["capacidad"] <= 0:   # «pending» solo no basta: AAP deja en espera trabajos de
            return False, f"AAP sin capacidad ({pend} trabajos pendientes)", det   # una misma plantilla aunque tenga capacidad
    except aap_mod.AAPNoDisponible:
        return False, "AAP no disponible", det
    except Exception as x:  # noqa: BLE001 - sin permiso para leer la capacidad: se lanza igual (AAP la administra)
        det["capacidad_error"] = str(x)[:120]
    return True, "", det


def _espera(n):
    return min(5 * (2 ** min(n, 6)), 120)


def _reclamar(cid):
    cur = con().execute("UPDATE cola_acciones SET estado='en_curso', tomado=?, tomado_por=?, actualizado=? WHERE id=? AND estado='pendiente'",
                        (time.time(), str(os.getpid()), ahora(), cid))
    con().commit()
    return cur.rowcount == 1


def _fallar(c, motivo, ticket=True):
    ej("UPDATE cola_acciones SET estado='fallido', secretos='', motivo=?, actualizado=? WHERE id=?", motivo[:500], ahora(), c["id"])
    if ticket and c["tipo"] == "lanzar":
        from .operacion import conector
        t = q1("SELECT * FROM tickets WHERE id=?", c["ticket_id"])
        if t and t["estado"] in ("en_cola", "pendiente_aprobacion") and not t.get("aap_job_id"):
            ej("UPDATE tickets SET estado='fallido', fin=?, resultado=? WHERE id=?", ahora(), f"No se pudo lanzar en AAP: {motivo}"[:1000], t["id"])
            try:
                a = q1("SELECT * FROM automatizaciones WHERE id=?", t["automatizacion_id"])
                cl = q1("SELECT * FROM clientes WHERE id=?", t["cliente_id"])
                conector(cl).cerrar(q1("SELECT * FROM tickets WHERE id=?", t["id"]), a)
            except Exception:  # noqa: BLE001
                pass
    print(f"cola: #{c['id']} ticket {c['ticket_id']} fallido · {motivo[:200]}", flush=True)


def _reintentar_luego(c, motivo, cuenta_intento):
    intentos = c["intentos"] + (1 if cuenta_intento else 0)
    if cuenta_intento and intentos >= MAX_INTENTOS:
        return _fallar(c, f"{motivo} (tras {intentos} intentos)")
    esperas = c["esperas"] + (0 if cuenta_intento else 1)
    ej("UPDATE cola_acciones SET estado='pendiente', intentos=?, esperas=?, proximo=?, motivo=?, actualizado=? WHERE id=?",
       intentos, esperas, time.time() + _espera(intentos if cuenta_intento else esperas), motivo[:300], ahora(), c["id"])


def _procesar(c, aap):
    from .aap import AAPNoDisponible, ErrorAAP
    t = q1("SELECT * FROM tickets WHERE id=?", c["ticket_id"])
    if not t:
        return _fallar(c, "el ticket ya no existe", ticket=False)
    datos = json.loads(c["datos"] or "{}")
    try:
        secretos = _descifrar(c["secretos"])
    except Exception:  # noqa: BLE001 - la clave de FaceOps cambió: sin sus claves no se lanza
        return _fallar(c, "las claves del ticket no se pudieron leer: vuelve a solicitarlo")
    if c["tipo"] == "lanzar":
        if t.get("aap_job_id"):
            ej("UPDATE cola_acciones SET estado='lanzado', secretos='', job_id=?, motivo='', actualizado=? WHERE id=?", t["aap_job_id"], ahora(), c["id"])
            return
        if t["estado"] not in ("en_cola", "pendiente_aprobacion"):
            ej("UPDATE cola_acciones SET estado='cancelado', secretos='', motivo=?, actualizado=? WHERE id=?",
               f"el ticket quedó {t['estado'].replace('_', ' ')} antes de lanzarse", ahora(), c["id"])
            return
        from .operacion import lanzar_ahora
        try:
            lanzar_ahora(t["id"], secretos, bool(datos.get("aprobado")))
        except AAPNoDisponible as x:
            return _reintentar_luego(c, f"AAP no disponible ({str(x)[:120]})", True)
        except ErrorAAP as x:   # lanzar_ahora ya dejó el ticket fallido con el motivo de AAP
            return _fallar(c, str(x)[:400], ticket=False)
        except Exception as x:  # noqa: BLE001
            return _reintentar_luego(c, f"{type(x).__name__}: {str(x)[:200]}", True)
        t2 = q1("SELECT * FROM tickets WHERE id=?", t["id"])
        if t2.get("aap_job_id"):
            ej("UPDATE cola_acciones SET estado='lanzado', secretos='', job_id=?, motivo='', actualizado=? WHERE id=?", t2["aap_job_id"], ahora(), c["id"])
            print(f"cola: #{c['id']} {t2['numero']} lanzado · job {t2['aap_job_id']}", flush=True)
        elif t2["estado"] == "fallido":
            _fallar(c, (t2["resultado"] or "AAP rechazó el lanzamiento")[:400], ticket=False)
        else:
            _reintentar_luego(c, "AAP no devolvió el trabajo", True)
    elif c["tipo"] == "aprobar":   # responder en AAP la etapa que se aprobó o rechazó en FaceOps
        try:
            aap.responder_aprobacion(datos["aprobacion"], bool(datos.get("aprobar")))
        except AAPNoDisponible as x:
            return _reintentar_luego(c, f"AAP no disponible ({str(x)[:120]})", True)
        except ErrorAAP as x:
            if "already been approved or denied" not in str(x):
                return _fallar(c, f"AAP no aceptó la respuesta: {str(x)[:300]}", ticket=False)
        ej("UPDATE cola_acciones SET estado='lanzado', motivo='', actualizado=? WHERE id=?", ahora(), c["id"])
        print(f"cola: #{c['id']} ticket {t['numero']} aprobación {'aprobada' if datos.get('aprobar') else 'rechazada'} en AAP", flush=True)
    else:
        _fallar(c, f"tipo de trabajo desconocido: {c['tipo']}", ticket=False)


def ciclo():
    """Una pasada por la cola. Devuelve cuántos trabajos atendió."""
    from .aap import AAP, ErrorAAP
    _tabla()
    if time.time() - _latido["v"] >= 10:
        _latido["v"] = time.time()
        ajuste("cola_latido", str(_latido["v"]))
    # trabajos «en curso» de un proceso que murió: vuelven a la cola
    ej("UPDATE cola_acciones SET estado='pendiente', motivo='retomado tras reinicio', actualizado=? WHERE estado='en_curso' AND tomado < ?",
       ahora(), time.time() - TOMADO_VENCE)
    pend = q("SELECT * FROM cola_acciones WHERE estado='pendiente' AND proximo <= ? ORDER BY id", time.time())
    hechos = 0
    for c in pend:   # primero el ticket en la herramienta del cliente (no depende de AAP)
        d_ = json.loads(c["datos"] or "{}")
        if c["tipo"] == "lanzar" and d_.get("externo") and not d_.get("externo_hecho"):
            from .operacion import crear_externo
            try:
                crear_externo(c["ticket_id"])
            except Exception as x:  # noqa: BLE001 - la herramienta del cliente no detiene la acción
                auditar("sistema", c["cliente_id"], "ticket externo no creado", str(c["ticket_id"]), {"error": str(x)[:300]})
            d_["externo_hecho"] = True
            ej("UPDATE cola_acciones SET datos=? WHERE id=?", json.dumps(d_, ensure_ascii=False), c["id"])
            c["datos"] = json.dumps(d_, ensure_ascii=False)
    for cid in dict.fromkeys(c["cliente_id"] for c in pend):
        suyos = [c for c in pend if c["cliente_id"] == cid]
        cl = q1("SELECT * FROM clientes WHERE id=?", cid)
        try:
            aap = AAP.de_cliente(cl)
        except ErrorAAP as x:
            for c in suyos:
                _fallar(c, str(x))
            continue
        ok, motivo, det = _salud_y_capacidad(aap)
        ajuste(f"cola_aap_{cid}", json.dumps({"ok": ok, "motivo": motivo, "revisado": time.time(), **det}))
        if not ok:   # AAP caído o sin capacidad: todo espera (sin gastar intentos), con espera creciente
            for c in suyos:
                if time.time() - (c["creado_ts"] or time.time()) > MAX_ESPERA_AAP:
                    _fallar(c, f"{motivo} durante más de {MAX_ESPERA_AAP // 3600} h")
                else:
                    _reintentar_luego(c, motivo, False)
            continue
        for c in suyos[:POR_CICLO]:
            if not _reclamar(c["id"]):
                continue
            c = q1("SELECT * FROM cola_acciones WHERE id=?", c["id"])
            try:
                _procesar(c, aap)
            except Exception as x:  # noqa: BLE001
                _reintentar_luego(c, f"{type(x).__name__}: {str(x)[:200]}", True)
            hechos += 1
    return hechos


def bucle():
    from . import aap as aap_mod
    aap_mod._iniciar_vigia = lambda: None   # en este proceso no corren el vigía ni el sincronizador de la consola web
    print(f"cola de acciones: proceso {os.getpid()} atendiendo", flush=True)
    while True:
        try:
            n = ciclo()
        except Exception as x:  # noqa: BLE001
            print("cola: error en el ciclo:", type(x).__name__, str(x)[:200], flush=True)
            n = 0
        if not n:
            time.sleep(ESPERA_CICLO)


# ── un solo proceso consumidor, levantado y vigilado por la consola web ──────────────────────────────────────────────────
def _candado():
    from . import bd
    return os.path.join(os.path.dirname(bd.RUTA), "cola-acciones.lock")


def main():
    import fcntl
    f = open(_candado(), "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("cola de acciones: ya hay un proceso atendiendo", flush=True)
        return
    f.write(str(os.getpid()))
    f.flush()
    bucle()


def _vivo():
    import fcntl
    try:
        f = open(_candado(), "a")
    except OSError:
        return True
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True   # el candado lo tiene el proceso de la cola
    fcntl.flock(f, fcntl.LOCK_UN)
    f.close()
    return False


_proc = {"p": None, "vigia": None}


def asegurar_proceso():
    """Levanta el proceso de la cola si no hay uno, y un hilo que lo vuelve a levantar si se cae (revisa cada 15 s)."""
    def levantar():
        if _proc["p"] is not None and _proc["p"].poll() is None:
            return
        if _vivo():
            return
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, FACEOPS_VIGIA="0", PYTHONPATH=raiz + os.pathsep + os.environ.get("PYTHONPATH", ""))
        _proc["p"] = subprocess.Popen([sys.executable, "-u", "-m", "app.cola"], cwd=raiz, env=env)

    def vigilar():
        while True:
            try:
                if _proc["p"] is not None:
                    _proc["p"].poll()   # recoge al hijo que terminó
                levantar()
            except Exception as x:  # noqa: BLE001
                print("cola: no se pudo levantar el proceso:", str(x)[:200], flush=True)
            time.sleep(15)

    try:
        _tabla()
    except Exception:  # noqa: BLE001
        pass
    levantar()
    if not (_proc["vigia"] and _proc["vigia"].is_alive()):
        _proc["vigia"] = threading.Thread(target=vigilar, daemon=True, name="faceops-cola-vigia")
        _proc["vigia"].start()


def latido():
    """Segundos desde la última pasada del proceso de la cola (None si nunca corrió)."""
    v = ajuste("cola_latido")
    return round(time.time() - float(v)) if v else None


if __name__ == "__main__":
    main()
