"""Historia ficticia para demostración: simula que el cliente está en el mes 6 del contrato. Crea tickets (FO-HIS-…) y
movimientos de créditos marcados con «demo-historia» para poder quitarlos sin tocar los datos reales.
Uso: from app.demo_historia import cargar, quitar; cargar(1) / quitar(1)"""
import json
import random
from datetime import datetime, timedelta, timezone

from .bd import ej, q, q1

MARCA = "demo-historia"
# segmento → (automatizaciones típicas, % de éxito, peso en el volumen)
PERFIL = {
    "App de pagos": ([22, 23, 25, 34, 43, 44, 46], 0.86, 9),
    "Ventas en línea": ([22, 23, 34, 1, 2, 3], 0.55, 4),
    "Catálogo en línea": ([1, 2, 3, 23], 0.6, 3),
    "Portal de clientes": ([23, 44, 45, 46], 0.72, 4),
    "Sitio web corporativo": ([1, 2, 3, 23], 0.5, 2),
    "Autoservicio de desarrollo": ([32, 34, 22, 24], 0.66, 3),
    "Gobierno de identidades": ([17, 18, 15, 37, 40], 0.9, 3),
    "Servidores corporativos": ([13, 6, 18, 20, 21], 0.92, 4),
    "Red corporativa": ([35, 36, 39, 41, 42], 0.9, 3),
    "Servicios en la nube": ([27, 31, 32, 33], 0.88, 2),
    "": ([20, 21, 28, 33, 42], 0.78, 3),
}
VOLUMEN = [9, 15, 22, 30, 38]   # tickets por mes, meses 1 a 5 (el mes 6 es el actual, con los datos reales)


def _mes(base, k):
    y, m = divmod(base.year * 12 + base.month - 1 + k, 12)
    return datetime(y, m + 1, 1, tzinfo=timezone.utc)


def cargar(cliente_id, meses_atras=5, semilla=7):
    if q1("SELECT 1 FROM tickets WHERE externo_ref=? AND cliente_id=?", MARCA, cliente_id):
        return "ya cargada"
    rnd = random.Random(semilla)
    hoy = datetime.now(timezone.utc)
    inicio = _mes(hoy, -meses_atras)                      # mes 1 del contrato
    autos = {a["id"]: a for a in q("SELECT id, nombre, minutos_manual FROM automatizaciones")}
    usuarios = [u["id"] for u in q("SELECT id FROM usuarios WHERE activo=1")] or [1]
    segs = list(PERFIL)
    pesos = [PERFIL[s][2] for s in segs]
    n = 0
    for k, vol in enumerate(VOLUMEN):
        mes = _mes(inicio, k)
        if mes >= _mes(hoy, 0):
            break
        fin_mes = _mes(mes, 1)
        # cargo fijo del mes (base del producto) si aún no existe
        per = mes.strftime("%Y-%m")
        if not q1("SELECT 1 FROM creditos_mov WHERE cliente_id=? AND tipo='base' AND periodo=?", cliente_id, per):
            ej("INSERT INTO creditos_mov(cliente_id, fecha, tipo, creditos, detalle, periodo, usuario) VALUES (?,?,?,?,?,?,?)",
               cliente_id, mes.isoformat(timespec="seconds"), "base", -100.0, f"Base del producto · {per}", per, MARCA)
        for _ in range(vol):
            s = rnd.choices(segs, pesos)[0]
            ids, exito, _p = PERFIL[s]
            aid = rnd.choice([i for i in ids if i in autos] or list(autos))
            a = autos[aid]
            creado = mes + timedelta(seconds=rnd.randint(0, int((fin_mes - mes).total_seconds()) - 3600))
            r = rnd.random()
            estado = "exitoso" if r < exito else rnd.choices(["fallido", "pendiente_aprobacion", "rechazado"], [0.7, 0.12, 0.18])[0]
            man = a["minutos_manual"] or 30
            unid = rnd.choice([1, 1, 1, 2, 3])
            dur = rnd.randint(15, 240) if estado in ("exitoso", "fallido") else None
            r2 = rnd.random()   # 60 % sin aprobación, 30 % de 5 a 45 min, 10 % de 1 a 6 h
            apr = 0 if r2 < 0.6 else (rnd.randint(300, 2700) if r2 < 0.9 else rnd.randint(3600, 6 * 3600))
            ok = estado == "exitoso"
            auto_min = (dur or 0) / 60
            factor = round(auto_min / man, 4) if ok and man else None
            cred = round(max(man - auto_min, 0) / 60 * max(man - auto_min, 0) / man * unid, 2) if ok else 0.0
            n += 1
            num = f"FO-HIS-{n:04d}"
            ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, parametros, creado, inicio, fin, "
               "valor_min, externo_ref, dur_ejecucion_s, dur_aprobacion_s, unidades, creditos, factor, ahorro) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               cliente_id, num, aid, rnd.choice(usuarios), estado, s, json.dumps({}), creado.isoformat(timespec="seconds"),
               (creado + timedelta(seconds=apr)).isoformat(timespec="seconds") if dur else None,
               (creado + timedelta(seconds=apr + (dur or 0))).isoformat(timespec="seconds") if dur else None,
               man * unid if ok else 0, MARCA, dur, apr if dur else None, unid, cred, factor, round(man * unid / 60 * 40, 2) if ok else 0.0)
            if dur and apr:   # la etapa de aprobación, con su área (se acumula a esa área)
                from .aprobacion_areas import area_de as _ar
                tid_ = q1("SELECT id FROM tickets WHERE numero=? AND cliente_id=?", num, cliente_id)["id"]
                ej("INSERT OR IGNORE INTO ticket_etapas(ticket_id, etapa, total, area, inicio, fin, estado, quien) VALUES (?,?,?,?,?,?,?,?)",
                   tid_, 1, 1, _ar(q1("SELECT * FROM automatizaciones WHERE id=?", aid)), creado.isoformat(timespec="seconds"),
                   (creado + timedelta(seconds=apr)).isoformat(timespec="seconds"), "rechazada" if estado == "rechazado" else "aprobada", "demo")
            if ok and cred:
                tid = q1("SELECT id FROM tickets WHERE numero=? AND cliente_id=?", num, cliente_id)["id"]
                ej("INSERT INTO creditos_mov(cliente_id, fecha, tipo, creditos, detalle, ticket_id, periodo, usuario) VALUES (?,?,?,?,?,?,?,?)",
                   cliente_id, (creado + timedelta(seconds=apr + (dur or 0))).isoformat(timespec="seconds"), "ejecucion", -cred, num, tid,
                   creado.strftime("%Y-%m"), MARCA)
    # algunas aprobaciones que siguen esperando (1 a 12 días)
    for s, aid, dias in (("Ventas en línea", 22, 9), ("Sitio web corporativo", 1, 4), ("Catálogo en línea", 34, 2), ("Autoservicio de desarrollo", 32, 5)):
        n += 1
        ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, parametros, creado, valor_min, externo_ref) "
           "VALUES (?,?,?,?,?,?,?,?,?,?)", cliente_id, f"FO-HIS-{n:04d}", aid, rnd.choice(usuarios), "pendiente_aprobacion", s, "{}",
           (hoy - timedelta(days=dias, hours=3)).isoformat(timespec="seconds"), 0, MARCA)
    # dos etapas: Redes ya aprobó, ahora espera Seguridad (los días de cada etapa se acumulan a su área)
    acc = q1("SELECT id FROM automatizaciones WHERE cliente_id=? AND nombre_aap='FaceOps · Acceso de red'", cliente_id)
    if acc:
        for dias_r, dias_s in ((3, 6), (1, 2)):
            n += 1
            num = f"FO-HIS-{n:04d}"
            cre = hoy - timedelta(days=dias_r + dias_s, hours=2)
            ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, parametros, creado, valor_min, externo_ref) "
               "VALUES (?,?,?,?,?,?,?,?,?,?)", cliente_id, num, acc["id"], rnd.choice(usuarios), "pendiente_aprobacion", "Red corporativa", "{}",
               cre.isoformat(timespec="seconds"), 0, MARCA)
            tid_ = q1("SELECT id FROM tickets WHERE numero=? AND cliente_id=?", num, cliente_id)["id"]
            ej("INSERT INTO ticket_etapas(ticket_id, etapa, total, area, inicio, fin, estado, quien) VALUES (?,?,?,?,?,?,?,?)",
               tid_, 1, 2, "redes", cre.isoformat(timespec="seconds"), (cre + timedelta(days=dias_r)).isoformat(timespec="seconds"), "aprobada", "demo")
            ej("INSERT INTO ticket_etapas(ticket_id, etapa, total, area, inicio, estado) VALUES (?,?,?,?,?,?)",
               tid_, 2, 2, "seguridad", (cre + timedelta(days=dias_r)).isoformat(timespec="seconds"), "pendiente")
    # el contrato parte en el mes 1 (año 1 con Managed Service)
    ini = inicio.date()
    ej("UPDATE clientes SET contrato_inicio=?, ms_hasta=? WHERE id=?", ini.isoformat(), ini.replace(year=ini.year + 1).isoformat(), cliente_id)
    from .deuda import sincronizar
    sincronizar(cliente_id)
    return f"{n} tickets ficticios desde {ini.isoformat()}"


def quitar(cliente_id):
    ids = [r["id"] for r in q("SELECT id FROM tickets WHERE externo_ref=? AND cliente_id=?", MARCA, cliente_id)]
    for i in ids:
        ej("DELETE FROM deudas WHERE ticket_id=?", i)
        ej("DELETE FROM ticket_etapas WHERE ticket_id=?", i)
        ej("DELETE FROM creditos_mov WHERE ticket_id=?", i)
    ej("DELETE FROM creditos_mov WHERE usuario=? AND cliente_id=?", MARCA, cliente_id)
    ej("DELETE FROM tickets WHERE externo_ref=? AND cliente_id=?", MARCA, cliente_id)
    return f"{len(ids)} tickets ficticios quitados"


def uso_automatizacion(cliente_id, aid, semilla=11):
    """Demostración: 12 meses de uso de una automatización (tickets FO-HIS-A…, marcados «demo-historia», removibles con quitar)."""
    rnd = random.Random(semilla + aid)
    a = q1("SELECT * FROM automatizaciones WHERE id=? AND cliente_id=?", aid, cliente_id)
    if not a:
        return "no existe"
    if q1("SELECT 1 FROM tickets WHERE automatizacion_id=? AND externo_ref=?", aid, MARCA):
        return "ya cargada"
    hoy = datetime.now(timezone.utc)
    usuarios = [u["id"] for u in q("SELECT id FROM usuarios WHERE activo=1")] or [1]
    man = a["minutos_manual"] or 30
    n = 0
    for k in range(11, -1, -1):   # de hace 11 meses al mes actual, con uso creciente
        mes = _mes(hoy, -k)
        fin_mes = min(_mes(mes, 1), hoy)
        vol = max(0, int(round(2 + (11 - k) * 0.9 + rnd.uniform(-1.5, 2))))
        for _ in range(vol):
            creado = mes + timedelta(seconds=rnd.randint(0, max(int((fin_mes - mes).total_seconds()) - 3600, 3600)))
            if creado > hoy:
                continue
            estado = "exitoso" if rnd.random() < 0.86 else rnd.choice(["fallido", "rechazado"])
            dur = rnd.randint(12, 90) if estado != "rechazado" else None
            apr = rnd.choice([0, 0, rnd.randint(300, 2400), rnd.randint(3600, 4 * 3600)])
            ok = estado == "exitoso"
            unid = rnd.choice([1, 1, 2])
            cred = round(max(man - (dur or 0) / 60, 0) / 60 * max(man - (dur or 0) / 60, 0) / man * unid, 2) if ok else 0.0
            n += 1
            num = f"FO-HIS-A{aid}-{n:03d}"
            fin = creado + timedelta(seconds=apr + (dur or 0))
            ej("INSERT INTO tickets(cliente_id, numero, automatizacion_id, usuario_id, estado, segmento, parametros, creado, inicio, fin, "
               "valor_min, externo_ref, dur_ejecucion_s, dur_aprobacion_s, unidades, creditos, ahorro) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               cliente_id, num, aid, rnd.choice(usuarios), estado, rnd.choice(["App de pagos", "Ventas en línea", "Servidores corporativos"]), "{}",
               creado.isoformat(timespec="seconds"), (creado + timedelta(seconds=apr)).isoformat(timespec="seconds"), fin.isoformat(timespec="seconds"),
               man * unid if ok else 0, MARCA, dur, apr, unid, cred, round(man * unid / 60 * 40, 2) if ok else 0.0)
            tid = q1("SELECT id FROM tickets WHERE numero=? AND cliente_id=?", num, cliente_id)["id"]
            from .aprobacion_areas import area_de
            ej("INSERT OR IGNORE INTO ticket_etapas(ticket_id, etapa, total, area, inicio, fin, estado, quien) VALUES (?,?,?,?,?,?,?,?)",
               tid, 1, 1, area_de(a), creado.isoformat(timespec="seconds"), (creado + timedelta(seconds=apr)).isoformat(timespec="seconds"),
               "rechazada" if estado == "rechazado" else "aprobada", "demo")
            if cred:
                ej("INSERT INTO creditos_mov(cliente_id, fecha, tipo, creditos, detalle, ticket_id, periodo, usuario) VALUES (?,?,?,?,?,?,?,?)",
                   cliente_id, fin.isoformat(timespec="seconds"), "ejecucion", -cred, num, tid, creado.strftime("%Y-%m"), MARCA)
    return f"{n} tickets de demostración para «{a['nombre']}»"
