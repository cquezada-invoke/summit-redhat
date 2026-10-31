#!/usr/bin/env python3
"""Plan de migración de un equipo (corre en el nodo de ejecución de AAP, no en el equipo).
Entrada (stdin, JSON): {"datos": {clave: valor recolectado}, "politica": minimo|mantenimiento|ultima, "tipo": rhel|centos|auto, "hoy": AAAA-MM-DD}
Salida (stdout, JSON): origen, soporte, destino, acción, camino (saltos), hallazgos por categoría y semáforo."""
import json
import re
import sys

# Ciclo de vida de RHEL (fechas de fin): mantenimiento estándar, ELS (add-on) y ELS Long-Life (add-on, términos anuales)
CICLO = {5: {"mant": "2017-03-31", "els": "2020-11-30"},
         6: {"mant": "2020-11-30", "els": "2024-06-30", "longlife": "2030-12-31"},
         7: {"mant": "2024-06-30", "els": "2029-05-31"},
         8: {"mant": "2029-05-31"}, 9: {"mant": "2032-05-31"}, 10: {"mant": "2035-05-31"}}
ULTIMA_MENOR = {6: 10, 7: 9, 8: 10}
METODO = {6: "Preupgrade Assistant + redhat-upgrade-tool", 7: "Leapp", 8: "Leapp", 9: "Leapp"}
CONVERT2RHEL = {"centos": (7, 8), "rocky": (8, 9), "almalinux": (8, 9), "ol": (7, 8)}
NIVEL = {"rojo": 3, "amarillo": 2, "info": 1}


def soporte(mayor, hoy):
    c = CICLO.get(mayor)
    if not c:
        return "desconocido", False
    if hoy <= c["mant"]:
        return f"mantenimiento hasta {c['mant'][:7]}", True
    if c.get("els") and hoy <= c["els"]:
        return f"solo con add-on ELS (hasta {c['els'][:7]})", True
    if c.get("longlife") and hoy <= c["longlife"]:
        return f"solo con ELS Long-Life (términos anuales hasta {c['longlife'][:4]})", True
    return "sin parches", False


def destino(mayor, politica, hoy):
    vers = sorted(CICLO)
    if politica == "ultima":
        return vers[-1]
    for m in vers:
        if m < mayor:
            continue
        c = CICLO[m]
        if politica == "mantenimiento" and hoy <= c["mant"]:
            return m
        if politica == "minimo" and (hoy <= c["mant"] or hoy <= c.get("els", "0")):
            return m
    return vers[-1]


def main():
    e = json.load(sys.stdin)
    d, pol, hoy, tipo = e["datos"], e.get("politica") or "minimo", e["hoy"], e.get("tipo") or "auto"
    rel = d.get("release", "")
    dist = (d.get("id") or "").lower()
    if "Red Hat" in rel:
        fam = "rhel"
    elif "CentOS" in rel or dist == "centos":
        fam, dist = "centos", "centos"
    elif dist in ("rocky", "almalinux", "ol") or any(x in rel for x in ("Rocky", "AlmaLinux", "Oracle")):
        fam = "otro_el"
        dist = dist or ("rocky" if "Rocky" in rel else "almalinux" if "Alma" in rel else "ol")
    else:
        fam = "otro"
    m = re.search(r"release (\d+)(?:\.(\d+))?", rel) or re.match(r"(\d+)(?:\.(\d+))?", d.get("version_id", ""))
    mayor, menor = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    nombre = {"rhel": "RHEL", "centos": "CentOS", "rocky": "Rocky Linux", "almalinux": "AlmaLinux", "ol": "Oracle Linux"}.get(fam if fam != "otro_el" else dist, rel)
    origen = f"{nombre} {mayor}.{menor}" if mayor else (rel or d.get("id", "?"))
    h = []

    def hall(nivel, cat, texto):
        h.append({"nivel": nivel, "categoria": cat, "texto": texto})

    if fam == "otro" or not mayor:
        return print(json.dumps({"origen": origen or "desconocido", "familia": fam, "accion": "no aplica", "semaforo": "gris",
                                 "camino": [], "hallazgos": [{"nivel": "info", "categoria": "Sistema", "texto": "No es RHEL ni un derivado de RHEL"}]}))
    if tipo == "rhel" and fam != "rhel":
        hall("rojo", "Sistema", f"Es {origen}: use «Migración · CentOS a RHEL»")
    if tipo == "centos" and fam == "rhel":
        hall("info", "Sistema", "Ya es RHEL: no hay conversión; use «Migración · RHEL a versión soportada»")

    sop, con_parches = soporte(mayor, hoy) if fam == "rhel" else ("sin soporte de Red Hat", False)
    dst = destino(mayor, pol, hoy)
    saltos, accion = [], ""
    convertir = fam in ("centos", "otro_el")
    if convertir:
        ok = mayor in CONVERT2RHEL.get(dist, ())
        if not ok:
            accion = "reconstruir"
            hall("rojo", "Sistema", f"Convert2RHEL no soporta {origen}: reconstruir en RHEL {max(dst, 8)} y migrar la aplicación")
    if mayor == 5:
        accion = "reconstruir"
        hall("rojo", "Sistema", f"RHEL 5 no tiene upgrade in-place: VM nueva en RHEL {dst} (recomendado RHEL {max(dst, 8)}) y migración de la aplicación")
    if not accion:
        pasos = []
        if convertir:
            pasos.append(f"Convertir {origen} → RHEL {mayor} (Convert2RHEL)")
        if mayor in ULTIMA_MENOR and menor < ULTIMA_MENOR[mayor]:
            pasos.append(f"Actualizar {mayor}.{menor} → {mayor}.{ULTIMA_MENOR[mayor]} (última versión menor)")
        for v in range(mayor, dst):
            fin = f"{v + 1}.{ULTIMA_MENOR[v + 1]}" if v + 1 in ULTIMA_MENOR else f"{v + 1}"
            pasos.append(f"{v}.{ULTIMA_MENOR.get(v, 'x')} → {fin} ({METODO[v]})")
            saltos.append(v)
        accion = ("convertir y migrar" if saltos else "convertir") if convertir else ("migrar" if saltos else ("actualizar versión menor" if pasos else "sin acción"))
    else:
        pasos = []

    # ---- hallazgos por categoría (lo que más rompe en una migración)
    final = dst if saltos else mayor
    cruza = lambda v: mayor < v <= final  # el salto atraviesa la versión v
    if accion == "reconstruir":
        final = 0
    flags = set((d.get("cpu_flags") or "").split())
    v2 = {"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"} <= flags
    v3 = v2 and {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "movbe", "xsave"} <= flags
    if d.get("arch") and d["arch"] != "x86_64":
        hall("amarillo", "Kernel y hardware", f"Arquitectura {d['arch']}: revisar el camino de upgrade soportado para ella")
    if final >= 9 and flags and not v2 and saltos:
        hall("rojo", "Kernel y hardware", "La CPU no cumple x86-64-v2, que exige RHEL 9")
    if final >= 10 and flags and not v3 and saltos:
        hall("rojo", "Kernel y hardware", "La CPU no cumple x86-64-v3, que exige RHEL 10")
    mods = (d.get("modulos_terceros") or "").split()
    if mods and final:
        hall("amarillo", "Kernel y hardware", f"Módulos de kernel de terceros: {', '.join(mods[:8])} — necesitan versión para RHEL {final}")
    reg = d.get("registrado", "")
    if fam == "rhel" and reg not in ("si", "satellite"):
        hall("rojo" if saltos or "actualizar" in accion else "amarillo", "Suscripción",
             "El equipo no está registrado (CDN o Satellite): sin repositorios no hay parches ni upgrade")
    if final == 7 and d.get("els") != "si":
        hall("amarillo", "Suscripción", "RHEL 7 solo recibe parches con el add-on ELS y no está habilitado: "
                                       "contratar ELS o usar la política «mantenimiento» (RHEL 8)")
    if final == 6 and fam == "rhel":
        hall("amarillo", "Suscripción", "RHEL 6 solo recibe parches con ELS Long-Life (términos anuales)")
    terceros = (d.get("repos_terceros") or "").split()
    if terceros and saltos:
        hall("amarillo", "Paquetes y repositorios", f"Repositorios de terceros: {', '.join(terceros[:6])} — se desactivan durante el upgrade; sus paquetes quedan para revisar")
    var_mb, boot_mb = int(d.get("var_libre_mb") or 0), int(d.get("boot_libre_mb") or 0)
    if saltos and var_mb and var_mb < 4096:
        hall("rojo", "Almacenamiento", f"/var tiene {var_mb} MB libres; el upgrade necesita al menos 4 GB")
    if saltos and boot_mb and boot_mb < 150:
        hall("amarillo", "Almacenamiento", f"/boot tiene {boot_mb} MB libres: borrar kernels antiguos antes")
    if int(d.get("luks") or 0) and saltos:
        hall("rojo" if 7 in saltos else "amarillo", "Almacenamiento",
             "Discos cifrados con LUKS: Leapp no migra 7→8 con LUKS; 8→9 solo con desbloqueo automático (Clevis/TPM2)")
    if int(d.get("nfs") or 0) and saltos:
        hall("amarillo", "Almacenamiento", "Montajes NFS en /etc/fstab: se deben desmontar durante el upgrade")
    if d.get("python2") == "si" and cruza(8):
        hall("amarillo", "Lenguajes y librerías", "Python 2 instalado: no existe en RHEL 8+ como intérprete base; revisar scripts y aplicaciones")
    if d.get("java"):
        hall("info", "Lenguajes y librerías", f"Java: {d['java']} — confirmar la versión soportada en RHEL {final}")
    if cruza(9):
        hall("amarillo", "Criptografía", "RHEL 9+ bloquea por defecto SHA-1 en firmas y TLS 1.0/1.1: revisar clientes y servidores antiguos")
    if d.get("fips") == "1":
        hall("amarillo", "Criptografía", "Modo FIPS activo: validar el procedimiento de upgrade en FIPS")
    if int(d.get("ifcfg") or 0) and cruza(10):
        hall("amarillo", "Red", "Configuración de red en formato ifcfg: RHEL 10 la elimina (Leapp la convierte a keyfile)")
    if d.get("network_scripts") == "si" and cruza(8):
        hall("amarillo", "Red", "Se usa network-scripts (sin NetworkManager): en RHEL 8+ la red la maneja NetworkManager")
    if d.get("ntpd") == "si" and cruza(8):
        hall("amarillo", "Servicios y arranque", "ntpd: en RHEL 8+ se reemplaza por chrony (Leapp migra la configuración)")
    if d.get("iptables") == "si" and cruza(8):
        hall("info", "Red", "Reglas con iptables: en RHEL 8+ el backend es nftables (firewalld)")
    if d.get("root_ssh_implicito") == "si" and 7 in saltos:
        hall("info", "Identidad y acceso", "PermitRootLogin no está explícito en sshd: se fija antes del upgrade para no perder el acceso")
    ag = (d.get("agentes") or "").split()
    if ag and final:
        hall("amarillo", "Herramientas y agentes", f"Agentes de terceros: {', '.join(ag[:8])} — confirmar soporte del fabricante en RHEL {final}")
    if d.get("apps") and final:
        hall("amarillo", "Aplicaciones", f"Aplicaciones detectadas: {d['apps']} — validar su certificación en RHEL {final}")
    if accion in ("migrar", "convertir y migrar", "convertir"):
        hall("info", "Operación", "Snapshot previo y vuelta atrás automática si falla; ventana sugerida: 2 a 3 horas por salto")

    peor = max((NIVEL.get(x["nivel"], 0) for x in h), default=0)
    sem = "reconstruir" if accion == "reconstruir" else {3: "rojo", 2: "amarillo"}.get(peor, "verde")
    if accion == "sin acción" and sem == "verde":
        sem = "al día"
    print(json.dumps({"origen": origen, "familia": fam, "distribucion": dist, "mayor": mayor, "menor": menor, "soporte": sop,
                      "con_parches": con_parches, "politica": pol, "destino": f"RHEL {dst if saltos or accion == 'reconstruir' else mayor}",
                      "destino_mayor": dst if saltos else mayor, "accion": accion, "convertir": convertir and accion != "reconstruir",
                      "saltos": saltos, "camino": pasos, "hallazgos": h, "semaforo": sem}, ensure_ascii=False))


main()
