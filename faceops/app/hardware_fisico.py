"""Servidores físicos: marca (para agrupar) y ficha de hardware del nodo (BMC, procesadores, memoria, discos, red, energía,
firmware y eventos). Lo que entrega el SMBIOS de la máquina (fabricante, modelo, serie, BIOS) es real; el resto se EMULA hasta
que exista la integración con el BMC de cada marca (Redfish: iDRAC, iLO, XClarity, CIMC, iRMC, iBMC). Lo emulado es
determinista por número de serie y queda marcado como tal en la ficha."""
import hashlib
import os
import re
from datetime import date, timedelta

import yaml

from .bd import j

_DEMO = {}


def demo(e):
    """Datos de demostración del nodo físico (datos/fisicos-demo.yml, por nombre): plataformas instaladas (hipervisor…) y su
    BMC simulado. En un cliente real estos datos llegan del inventario; el archivo solo existe para el laboratorio."""
    ruta = os.path.join(os.path.dirname(__file__), "..", "datos", "fisicos-demo.yml")
    try:
        mt = os.path.getmtime(ruta)
    except OSError:
        return {}
    if _DEMO.get("mt") != mt:
        with open(ruta, encoding="utf-8") as f_:
            _DEMO.update(mt=mt, d=(yaml.safe_load(f_) or {}).get("equipos") or {})
    return _DEMO["d"].get(e.get("nombre") or "") or _DEMO["d"].get(f"#{e.get('id')}") or {}


def plataformas_nodo(e):
    """Plataformas instaladas en el nodo físico (hipervisor y otras): las del inventario (datos.plataformas_nodo) o, en el
    laboratorio, las de datos/fisicos-demo.yml. [{codigo, nombre, version, edicion, licencia, vcenter, infra}]"""
    d = e.get("_datos") if isinstance(e.get("_datos"), dict) else (j(e.get("datos"), {}) or {})
    return d.get("plataformas_nodo") or demo(e).get("plataformas") or []

MARCAS = [  # (clave, nombre, patrón del fabricante, BMC, color)
    ("dell", "Dell", r"dell", "iDRAC9", "#0076ce"),
    ("hpe", "HPE", r"\bhpe\b|hewlett|\bhp\b", "iLO 6", "#01a982"),
    ("lenovo", "Lenovo", r"lenovo", "XClarity Controller 2", "#e2231a"),
    ("cisco", "Cisco", r"cisco", "Cisco IMC", "#049fd9"),
    ("supermicro", "Supermicro", r"supermicro", "Supermicro BMC", "#2b6cb0"),
    ("fujitsu", "Fujitsu", r"fujitsu", "iRMC S6", "#e60012"),
    ("xfusion", "xFusion", r"xfusion|huawei", "iBMC", "#c7000b"),
]
SIN_MARCA = ("otra", "Sin marca identificada", "", "BMC", "#8a8d90")

# Perfil de hardware por modelo (lo que tendría un nodo de ese modelo); clave = patrón del modelo
MODELOS = [
    (r"r760", {"cpu": ("Intel Xeon Gold 6430", 2, 32, 2.1), "dimm": (16, 64, "DDR5-4800 RDIMM"), "raid": "PERC H965i Front",
               "discos": [("SSD SAS 3.84 TB", 6), ("SSD SATA 480 GB (BOSS-N1)", 2)], "nics": [("Broadcom 57508 2x100GbE OCP", 2, 100), ("Broadcom 5720 2x1GbE LOM", 2, 1)],
               "psu": (2, 1400), "ventiladores": 6, "u": 2}),
    (r"r660", {"cpu": ("Intel Xeon Silver 4416+", 2, 20, 2.0), "dimm": (8, 32, "DDR5-4800 RDIMM"), "raid": "PERC H755 Front",
               "discos": [("SSD SAS 1.92 TB", 4), ("SSD M.2 480 GB (BOSS-N1)", 2)], "nics": [("Broadcom 57414 2x25GbE OCP", 2, 25), ("Broadcom 5720 2x1GbE LOM", 2, 1)],
               "psu": (2, 800), "ventiladores": 8, "u": 1}),
    (r"dl380", {"cpu": ("Intel Xeon Gold 6448Y", 2, 32, 2.1), "dimm": (16, 64, "DDR5-4800 RDIMM"), "raid": "HPE MR416i-o Gen11",
                "discos": [("SSD SAS 3.84 TB", 8), ("SSD NVMe 480 GB (NS204i-u boot)", 2)], "nics": [("HPE Ethernet 25Gb 2p SFP28 OCP3", 2, 25), ("Intel I350 4x1GbE", 4, 1)],
                "psu": (2, 1600), "ventiladores": 6, "u": 2}),
    (r"dl360", {"cpu": ("Intel Xeon Gold 5418Y", 2, 24, 2.0), "dimm": (8, 32, "DDR5-4800 RDIMM"), "raid": "HPE MR408i-o Gen11",
                "discos": [("SSD SAS 1.92 TB", 4), ("SSD NVMe 480 GB (NS204i-u boot)", 2)], "nics": [("HPE Ethernet 10/25Gb 2p SFP28 OCP3", 2, 25)],
                "psu": (2, 1000), "ventiladores": 7, "u": 1}),
    (r"sr650", {"cpu": ("Intel Xeon Gold 6442Y", 2, 24, 2.6), "dimm": (16, 64, "DDR5-4800 RDIMM"), "raid": "ThinkSystem RAID 940-16i 8GB",
                "discos": [("SSD SAS 3.84 TB", 6), ("SSD M.2 480 GB (boot RAID 1)", 2)], "nics": [("Mellanox ConnectX-6 Lx 2x25GbE OCP", 2, 25), ("Broadcom 5719 4x1GbE", 4, 1)],
                "psu": (2, 1100), "ventiladores": 6, "u": 2}),
    (r"sr630", {"cpu": ("Intel Xeon Silver 4410Y", 2, 12, 2.0), "dimm": (8, 32, "DDR5-4800 RDIMM"), "raid": "ThinkSystem RAID 540-8i",
                "discos": [("SSD SATA 1.92 TB", 4), ("SSD M.2 240 GB (boot RAID 1)", 2)], "nics": [("Intel E810-XXVDA2 2x25GbE OCP", 2, 25)],
                "psu": (2, 750), "ventiladores": 8, "u": 1}),
    (r"c240", {"cpu": ("Intel Xeon Gold 6438N", 2, 32, 2.0), "dimm": (16, 64, "DDR5-4800 RDIMM"), "raid": "Cisco 24G Tri-Mode M1 RAID 4GB",
               "discos": [("SSD SAS 3.8 TB", 8), ("SSD M.2 960 GB (boot RAID 1)", 2)], "nics": [("Cisco VIC 15428 4x10/25/50GbE mLOM", 4, 25), ("Intel X710 2x10GbE", 2, 10)],
               "psu": (2, 2300), "ventiladores": 6, "u": 2}),
    (r"221h|sys-", {"cpu": ("Intel Xeon Gold 6430", 2, 32, 2.1), "dimm": (16, 32, "DDR5-4800 RDIMM"), "raid": "Broadcom MegaRAID 9560-8i",
                    "discos": [("SSD NVMe U.2 3.84 TB", 4), ("SSD M.2 NVMe 480 GB (boot)", 2)], "nics": [("Supermicro AOC-S25GC-i2S 2x25GbE", 2, 25), ("Intel X710 2x10GbE (onboard)", 2, 10)],
                    "psu": (2, 2000), "ventiladores": 6, "u": 2}),
    (r"rx2540", {"cpu": ("Intel Xeon Gold 6426Y", 2, 16, 2.5), "dimm": (12, 64, "DDR5-4800 RDIMM"), "raid": "PRAID EP680i",
                 "discos": [("SSD SAS 1.92 TB", 6), ("SSD M.2 240 GB (Dual M.2 boot)", 2)], "nics": [("PLAN EP X710-DA2 2x10GbE", 2, 10), ("PLAN EM 2x25GbE OCP3", 2, 25)],
                 "psu": (2, 1300), "ventiladores": 6, "u": 2}),
    (r"2288h", {"cpu": ("Intel Xeon Gold 6438Y+", 2, 32, 2.0), "dimm": (16, 64, "DDR5-4800 RDIMM"), "raid": "Avago SAS3908 4GB",
                "discos": [("SSD SAS 3.84 TB", 8), ("SSD SATA 480 GB (boot RAID 1)", 2)], "nics": [("xFusion SP670 2x25GbE OCP", 2, 25), ("xFusion SP333 4xGE", 4, 1)],
                "psu": (2, 1500), "ventiladores": 4, "u": 2}),
]
GENERICO = {"cpu": ("Intel Xeon Silver 4410Y", 2, 12, 2.0), "dimm": (8, 32, "DDR5-4800 RDIMM"), "raid": "Controladora RAID",
            "discos": [("SSD SAS 1.92 TB", 4)], "nics": [("2x25GbE", 2, 25)], "psu": (2, 1000), "ventiladores": 6, "u": 2}


def _hw(e):
    d = e.get("_datos") if isinstance(e.get("_datos"), dict) else (j(e.get("datos"), {}) or {})
    return d.get("hardware") or {}


def es_fisico(e):
    return (e.get("forma") or "") != "virtual" and (e.get("categoria") or "servidor") in ("servidor", "almacenamiento", "estación de trabajo")


def marca_de(e):
    """(clave, nombre, bmc, color) de la marca del servidor según su fabricante (SMBIOS)."""
    fab = (_hw(e).get("fabricante") or "") + " " + (e.get("nombre") or "")
    for k, n, rx, bmc, col in MARCAS:
        if re.search(rx, fab, re.I):
            return {"clave": k, "nombre": n, "bmc": bmc, "color": col}
    k, n, _, bmc, col = SIN_MARCA
    return {"clave": k, "nombre": n, "bmc": bmc, "color": col}


def agrupar(eqs):
    """Cajas por marca: [{clave, nombre, color, equipos, modelos}] ordenadas por cantidad."""
    gs = {}
    for e in eqs:
        m = marca_de(e)
        x = gs.setdefault(m["clave"], dict(m, equipos=[], modelos={}))
        x["equipos"].append(e)
        mod = _hw(e).get("modelo") or "modelo sin identificar"
        x["modelos"][mod] = x["modelos"].get(mod, 0) + 1
    return sorted(gs.values(), key=lambda x: (x["clave"] == "otra", -len(x["equipos"]), x["nombre"]))


def _r(serie, sal, n):
    return int(hashlib.sha256(f"{serie}:{sal}".encode()).hexdigest(), 16) % n


def ficha(e):
    """Ficha de hardware del nodo físico. Real: fabricante, modelo, serie, BIOS. Emulado: lo demás (marcado)."""
    hw, m = _hw(e), marca_de(e)
    modelo = hw.get("modelo") or ""
    serie = hw.get("serie") or e.get("nombre") or "x"
    p = next((v for rx, v in MODELOS if re.search(rx, modelo, re.I)), GENERICO)
    cpu, sockets, cores, ghz = p["cpu"]
    n_dimm, gb_dimm, tipo_dimm = p["dimm"]
    oct3 = 10 + _r(serie, "ip", 200)
    fw_bmc = {"dell": "7.10.30.00", "hpe": "1.59", "lenovo": "ESX316J 4.20", "cisco": "4.3(2.240002)", "supermicro": "01.03.12", "fujitsu": "3.12P", "xfusion": "3.08.00.36"}.get(m["clave"], "1.0")
    inicio = date(2024, 1, 15) + timedelta(days=_r(serie, "compra", 600))
    fin_g = inicio + timedelta(days=365 * 5)
    hoy = date.today()
    # memoria: un DIMM con errores corregibles en algunos equipos
    dimm_mal = _r(serie, "dimm", 4) == 0
    dimms = [{"slot": f"{'AB'[i // (n_dimm // 2)]}{i % (n_dimm // 2) + 1}", "gb": gb_dimm, "tipo": tipo_dimm,
              "fabricante": ["Samsung", "SK hynix", "Micron"][_r(serie, f"mem{i}", 3)],
              "estado": "advertencia · errores corregibles (ECC)" if dimm_mal and i == 3 else "OK"} for i in range(n_dimm)]
    # discos: uno predictivo en algunos equipos
    disco_mal = _r(serie, "disco", 3) == 0
    discos, k = [], 0
    for desc, n in p["discos"]:
        for i in range(n):
            k += 1
            boot = "boot" in desc or "BOSS" in desc
            discos.append({"bahia": ('M.2-' if boot else 'Bahía ') + str(i), "desc": desc, "vd": "Sistema (RAID 1)" if boot else "Datos (RAID 10)" if n >= 4 else "Datos (RAID 1)",
                           "estado": "predicción de falla (SMART)" if disco_mal and k == 2 else "en línea",
                           "vida": 100 - _r(serie, f"vida{k}", 18), "fw": ["HPD3", "BC02", "4.1.9", "C204"][_r(serie, f"dfw{k}", 4)]})
    nics = []
    for desc, puertos, vel in p["nics"]:
        for i in range(puertos):
            nics.append({"tarjeta": desc, "puerto": i + 1, "velocidad": f"{vel} Gb/s",
                         "enlace": "arriba" if i < 2 else "sin cable", "mac": ":".join(f"{_r(serie, f'{desc}{i}{b}', 256):02x}" for b in range(6))})
    psu_n, psu_w = p["psu"]
    psu_mal = _r(serie, "psu", 5) == 0
    consumo = int(psu_w * (0.28 + _r(serie, "w", 20) / 100))
    temps = [("Entrada (ambiente)", 21 + _r(serie, "t1", 6), 42), ("CPU 1", 48 + _r(serie, "t2", 18), 95), ("CPU 2", 47 + _r(serie, "t3", 18), 95),
             ("Memoria", 34 + _r(serie, "t4", 10), 85), ("Salida", 33 + _r(serie, "t5", 9), 70)]
    fw = [("BIOS", hw.get("bios") or "—", None), (m["bmc"], fw_bmc, None), ("Controladora " + p["raid"], ["52.26.0-5179", "8.7.0.0.18", "5.270.02-3953"][_r(serie, "rfw", 3)], None),
          (p["nics"][0][0], ["22.31.6", "21.85.21.91", "4.30"][_r(serie, "nfw", 3)], None)]
    # el BIOS o el BMC atrasados respecto de lo recomendado por el fabricante en algunos equipos
    atraso = _r(serie, "fwatr", 3)
    nueva = {"dell": "2.3.5", "hpe": "U54 v2.22", "lenovo": "ESE124H-3.30", "cisco": "C240M7.4.3.4", "supermicro": "2.3", "fujitsu": "R1.21.0", "xfusion": "2.67"}.get(m["clave"], "más reciente")
    fw = [{"componente": c, "version": v, "al_dia": not (i == 0 and atraso == 0), "recomendada": nueva if (i == 0 and atraso == 0) else v}
          for i, (c, v, _) in enumerate(fw)]
    eventos = []
    if disco_mal:
        eventos.append(("advertencia", "Disco en Bahía 1: predicción de falla (SMART). Programar reemplazo."))
    if dimm_mal:
        eventos.append(("advertencia", f"DIMM {dimms[3]['slot']}: errores corregibles por sobre el umbral"))
    if psu_mal:
        eventos.append(("crítico", "Fuente de poder 2: sin entrada de energía (redundancia perdida)"))
    eventos += [("info", f"{m['bmc']}: inicio de sesión desde la red de administración"), ("info", "Sistema encendido (botón o comando remoto)"),
                ("info", "Registro de eventos del sistema (SEL) depurado")]
    alertas = sum(1 for s, _ in eventos if s != "info") + sum(1 for x in fw if not x["al_dia"])
    return {
        "emulado": True, "marca": m, "modelo": modelo or "—", "serie": hw.get("serie") or "—", "bios": hw.get("bios") or "—", "u": p["u"],
        "estado": "crítico" if psu_mal else ("advertencia" if alertas else "OK"), "alertas": alertas,
        "bmc": dict({"tipo": m["bmc"], "firmware": fw_bmc, "ip": f"10.20.{oct3}.{10 + _r(serie, 'ipb', 200)}", "redfish": True,
                     "licencia": {"dell": "iDRAC9 Enterprise", "hpe": "iLO Advanced", "lenovo": "XCC2 Platinum", "cisco": "IMC estándar"}.get(m["clave"], "estándar"),
                     "credencial": f"{e.get('nombre')}-bmc", "puerto": 22, "web": None}, **(demo(e).get("bmc") or {})),
        "garantia": {"inicio": inicio.isoformat(), "fin": fin_g.isoformat(), "dias": (fin_g - hoy).days,
                     "nivel": ["ProSupport Plus 4h", "Foundation Care 24x7", "Premier Support", "Smart Net Total Care", "Soporte estándar NBD"][_r(serie, "gar", 5)]},
        "cpu": {"modelo": cpu, "sockets": sockets, "cores_socket": cores, "cores": sockets * cores, "ghz": ghz},
        "memoria": {"total_gb": n_dimm * gb_dimm, "slots": n_dimm * 2, "dimms": dimms},
        "raid": p["raid"], "discos": discos, "nics": nics,
        "energia": {"psus": [{"n": i + 1, "w": psu_w, "estado": "sin entrada" if psu_mal and i == 1 else "OK"} for i in range(psu_n)],
                    "consumo_w": consumo, "pico_w": int(consumo * 1.35), "redundancia": "perdida" if psu_mal else "completa"},
        "temperaturas": [{"sensor": s, "c": c, "max": mx, "pct": round(100 * c / mx)} for s, c, mx in temps],
        "ventiladores": [{"n": i + 1, "rpm": 5200 + _r(serie, f"fan{i}", 2600)} for i in range(p["ventiladores"])],
        "firmware": fw, "eventos": eventos,
    }
