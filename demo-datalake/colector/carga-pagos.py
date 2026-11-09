"""Prueba de carga de la App de pagos: cientos de miles de requests de negocio por el balanceador (nginx) de la aplicación.
Como la derivación al data lake está activa, cada request se copia al lake (espejo) y JavIA la analiza. Los cuerpos llevan una
fecha de operación simulada en los últimos 90 días (historia de prueba) con tendencias de uso realistas: el QR crece, las
transferencias caen, la app móvil gana participación, hay un pico de reembolsos en un rubro y la web confirma menos pagos.
Variables: CARGA_BASE (https://ip-del-balanceador), CARGA_TOTAL (300000), CARGA_HILOS (48). Solo stdlib."""
import http.client
import json
import os
import random
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

BASE = os.environ.get("CARGA_BASE", "https://192.168.122.11")
TOTAL = int(os.environ.get("CARGA_TOTAL", "300000"))
HILOS = int(os.environ.get("CARGA_HILOS", "48"))
PRE = "/app-jboss"
HOY = datetime.now(timezone.utc).replace(microsecond=0)
DIAS = 90
RUBROS = ["Supermercado", "Farmacia", "Combustible", "Restaurantes", "Tecnología", "Vestuario", "Viajes", "Servicios básicos"]
REGIONES = ["Metropolitana", "Valparaíso", "Biobío", "Antofagasta", "Los Lagos", "Coquimbo"]
CLIENTES = 20000
u = urlsplit(BASE)
CTX = ssl._create_unverified_context()
cont = {"ok": 0, "err": 0}
lock = threading.Lock()


def momento():
    """Fecha de operación simulada: más uso en días hábiles y en horas punta; un poco más de uso reciente."""
    d = int(DIAS * (random.random() ** 0.85))
    t = HOY - timedelta(days=d)
    h = random.choices(range(24), weights=[1, .6, .4, .3, .3, .5, 1.5, 3, 4, 4.5, 5, 5, 6, 5.5, 5, 5, 5, 5.5, 6, 6, 5, 4, 3, 2])[0]
    return t.replace(hour=h, minute=random.randrange(60), second=random.randrange(60)), d


def canal(d):
    reciente = (DIAS - d) / DIAS            # 0 = hace 90 días, 1 = hoy
    return random.choices(["app", "web", "pos", "api"], weights=[40 + 25 * reciente, 30 - 12 * reciente, 20 - 8 * reciente, 10])[0]


def operacion():
    t, d = momento()
    reciente = (DIAS - d) / DIAS
    cli = random.randint(1, CLIENTES)
    perfil = cli % 4                           # perfiles de uso: 0 pagador, 1 QR intensivo, 2 transferidor, 3 comercio
    pesos = {"pagar": 30, "consultar": 14, "confirmar": 22, "qr": 6 + 18 * reciente, "transferir": 14 - 9 * reciente, "saldo": 10,
             "reembolso": 2, "liquidacion": 3, "login": 8, "anular": 1.5}
    if perfil == 1:
        pesos["qr"] *= 3
    if perfil == 2:
        pesos["transferir"] *= 3
    if perfil == 3:
        pesos["liquidacion"] *= 5
    if d < 12:
        pesos["reembolso"] *= 6                  # pico reciente de reembolsos (rubro Tecnología)
    op = random.choices(list(pesos), weights=list(pesos.values()))[0]
    c = canal(d)
    rubro = "Tecnología" if op == "reembolso" and random.random() < .7 else random.choice(RUBROS)
    pid = f"PAG-{random.randint(1, 10**8):08d}"
    cuerpo = {"fecha": t.isoformat().replace("+00:00", "Z"), "cliente_id": cli, "canal": c, "rubro": rubro, "region": random.choice(REGIONES)}
    if op == "pagar":
        return "POST", f"{PRE}/pagos", dict(cuerpo, id=pid, monto=round(random.lognormvariate(3.3, 1.0), 2), moneda="USD", medio=random.choice(["débito", "crédito", "prepago"]))
    if op == "confirmar":
        if c == "web" and reciente > .8 and random.random() < .45:
            return "GET", f"{PRE}/pagos/{pid}", cuerpo   # la web confirma menos en las últimas semanas: se queda en la consulta
        return "POST", f"{PRE}/pagos/{pid}/confirmar", dict(cuerpo, id=pid, resultado="aprobado")
    if op == "consultar":
        return "GET", f"{PRE}/pagos/{pid}", cuerpo
    if op == "anular":
        return "POST", f"{PRE}/pagos/{pid}/anular", dict(cuerpo, id=pid, motivo=random.choice(["duplicado", "error de monto", "arrepentimiento"]))
    if op == "qr":
        return "POST", f"{PRE}/qr/pagar", dict(cuerpo, monto=round(random.lognormvariate(2.6, .8), 2), comercio=f"COM-{random.randint(1, 900):04d}")
    if op == "transferir":
        return "POST", f"{PRE}/transferencias", dict(cuerpo, monto=round(random.lognormvariate(4.2, 1.0), 2), destino=f"CTA-{random.randint(1, 10**6):06d}")
    if op == "saldo":
        return "GET", f"{PRE}/saldo", cuerpo
    if op == "reembolso":
        return "POST", f"{PRE}/reembolsos", dict(cuerpo, id=pid, monto=round(random.lognormvariate(3.5, .9), 2))
    if op == "liquidacion":
        return "GET", f"{PRE}/comercios/COM-{random.randint(1, 900):04d}/liquidacion", cuerpo
    return "POST", f"{PRE}/login", dict(cuerpo, dispositivo=random.choice(["iOS", "Android", "Navegador"]))


def trabajador(n):
    con = None
    for _ in range(n):
        m, ruta, cuerpo = operacion()
        datos = json.dumps(cuerpo, ensure_ascii=False).encode()
        for intento in range(2):
            try:
                if con is None:
                    con = http.client.HTTPSConnection(u.hostname, u.port or 443, context=CTX, timeout=15)
                if m == "GET":   # el GET lleva sus datos de negocio en el cuerpo para que el lake los conserve (prueba)
                    con.request(m, ruta, body=datos, headers={"Content-Type": "application/json", "User-Agent": "FaceOps-carga/1.0"})
                else:
                    con.request(m, ruta, body=datos, headers={"Content-Type": "application/json", "User-Agent": "FaceOps-carga/1.0"})
                con.getresponse().read()
                with lock:
                    cont["ok"] += 1
                break
            except Exception:  # noqa: BLE001
                con = None
                if intento:
                    with lock:
                        cont["err"] += 1


if __name__ == "__main__":
    t0 = time.time()
    por = TOTAL // HILOS
    hs = [threading.Thread(target=trabajador, args=(por,), daemon=True) for _ in range(HILOS)]
    for h in hs:
        h.start()
    while any(h.is_alive() for h in hs):
        time.sleep(15)
        dt = time.time() - t0
        print(f"{int(dt)} s · enviados {cont['ok']} · errores {cont['err']} · {cont['ok'] / max(dt, 1):.0f} req/s", flush=True)
    print(f"FIN: {cont['ok']} requests en {int(time.time() - t0)} s ({cont['err']} errores)", flush=True)
