"""Denuncias sintéticas de una fiscalía de una gran ciudad (12 meses, 16 alcaldías). Semilla fija: siempre los mismos datos.
Ningún dato es real: folios, agentes del Ministerio Público y hechos son inventados para la demostración."""
import datetime as dt

import numpy as np
import pandas as pd

ALCALDIAS = [("Iztapalapa", 16), ("Gustavo A. Madero", 11), ("Cuauhtémoc", 12), ("Álvaro Obregón", 7), ("Coyoacán", 6),
             ("Tlalpan", 6), ("Benito Juárez", 6), ("Venustiano Carranza", 6), ("Azcapotzalco", 4), ("Miguel Hidalgo", 5),
             ("Iztacalco", 4), ("Xochimilco", 4), ("Tláhuac", 3), ("La Magdalena Contreras", 2), ("Cuajimalpa", 2), ("Milpa Alta", 1)]
# delito: (categoría, peso, unidad especializada o None, base de judicialización, días medios de resolución)
DELITOS = {
    "Robo a transeúnte": ("Patrimonio", 18, None, -1.6, 75),
    "Robo de vehículo": ("Patrimonio", 9, "Fiscalía de Robo de Vehículos", -1.9, 95),
    "Robo a negocio": ("Patrimonio", 7, None, -1.2, 70),
    "Robo a casa habitación": ("Patrimonio", 5, None, -1.7, 90),
    "Fraude": ("Patrimonio", 10, "Fiscalía de Delitos Financieros", -2.0, 140),
    "Abuso de confianza": ("Patrimonio", 4, "Fiscalía de Delitos Financieros", -1.8, 120),
    "Extorsión": ("Patrimonio", 3, "Fiscalía Antiextorsión", -1.4, 110),
    "Daño a la propiedad": ("Patrimonio", 6, None, -1.3, 55),
    "Violencia familiar": ("Familia", 16, "Fiscalía de Violencia Familiar", -0.9, 60),
    "Lesiones dolosas": ("Personas", 7, None, -0.8, 65),
    "Amenazas": ("Personas", 8, None, -1.5, 50),
    "Homicidio doloso": ("Personas", 1, "Fiscalía de Homicidios", -0.4, 160),
    "Narcomenudeo": ("Seguridad pública", 6, "Fiscalía de Narcomenudeo", 0.3, 45),
}
CANALES = [("Presencial en agencia del MP", 45), ("Denuncia digital", 30), ("MP Virtual (teléfono)", 15), ("Puesta a disposición con detenido", 10)]
ESTADOS_CIERRE = ["Judicializada", "Acuerdo reparatorio", "Archivo temporal", "No ejercicio de la acción penal"]
HOY = dt.datetime(2026, 11, 6, 9, 0)
INICIO = dt.datetime(2025, 11, 1)


def unidad_de(delito, alcaldia):
    esp = DELITOS[delito][2]
    return esp or f"Fiscalía Territorial {alcaldia}"


def generar(n=36000, semilla=11):
    rng = np.random.default_rng(semilla)
    pa = np.array([a[1] for a in ALCALDIAS], float); pa /= pa.sum()
    nd = list(DELITOS); pd_ = np.array([DELITOS[d][1] for d in nd], float); pd_ /= pd_.sum()
    pc = np.array([c[1] for c in CANALES], float); pc /= pc.sum()
    alc = [ALCALDIAS[i][0] for i in rng.choice(len(ALCALDIAS), n, p=pa)]
    deli = [nd[i] for i in rng.choice(len(nd), n, p=pd_)]
    canal = [CANALES[i][0] for i in rng.choice(len(CANALES), n, p=pc)]
    dias_tot = (HOY - INICIO).days
    # más denuncias en diciembre y en fin de semana; tendencia levemente creciente
    off = np.sort(rng.beta(1.08, 1.0, n) * dias_tot)
    fechas = [INICIO + dt.timedelta(days=float(o), hours=float(rng.integers(7, 23)), minutes=float(rng.integers(0, 60))) for o in off]
    det = np.array([c.startswith("Puesta") or (d == "Narcomenudeo" and rng.random() < .5) for c, d in zip(canal, deli)])
    evid = rng.poisson(1.4, n).clip(0, 6)
    video = rng.random(n) < .32
    testigos = rng.random(n) < .38
    ident = rng.random(n) < np.where(np.isin(deli, ["Violencia familiar", "Amenazas", "Lesiones dolosas", "Abuso de confianza"]), .8, .25)
    carga = rng.normal(75, 22, n).clip(15, 160).round().astype(int)
    mp = [f"MP-{a[:3].upper().replace('Á', 'A')}-{int(rng.integers(1, 9)):02d}" for a in alc]
    base = np.array([DELITOS[d][3] for d in deli])
    logit = base - 1.7 + 2.6 * det + .32 * evid + .55 * video + .45 * testigos + .7 * ident - .014 * (carga - 75)
    p_jud = 1 / (1 + np.exp(-logit))
    media = np.array([DELITOS[d][4] for d in deli]) * (1 + (carga - 75) / 150) * np.where(det, .45, 1) * (1 - .05 * evid)
    dias = rng.gamma(2.2, np.maximum(media, 8) / 2.2).round().astype(int).clip(1, 400)
    filas = []
    for i in range(n):
        cierre = fechas[i] + dt.timedelta(days=int(dias[i]))
        if cierre > HOY:
            estado, fc, dr = "En trámite", None, None
        else:
            u = rng.random()
            estado = "Judicializada" if u < p_jud[i] else ("Acuerdo reparatorio" if u < p_jud[i] + .18 * (1 - p_jud[i]) and ident[i]
                                                         else ("Archivo temporal" if rng.random() < .7 else "No ejercicio de la acción penal"))
            fc, dr = cierre, int(dias[i])
        pr = "Alta" if (deli[i] in ("Homicidio doloso", "Extorsión", "Violencia familiar") or det[i]) else ("Media" if deli[i] in ("Lesiones dolosas", "Robo a casa habitación", "Robo de vehículo") else "Baja")
        filas.append((f"CI-{alc[i][:3].upper().replace('Á', 'A')}-{fechas[i]:%y}-{i + 1:06d}", fechas[i], alc[i], deli[i], DELITOS[deli[i]][0], canal[i],
                      unidad_de(deli[i], alc[i]), mp[i], pr, bool(det[i]), int(evid[i]), bool(video[i]), bool(testigos[i]), bool(ident[i]),
                      int(carga[i]), estado, fc, dr))
    return pd.DataFrame(filas, columns=["folio", "fecha_denuncia", "alcaldia", "delito", "categoria", "canal", "unidad", "mp", "prioridad",
                                        "hay_detenido", "evidencias", "video", "testigos", "identifica_responsable", "carga_mp",
                                        "estado", "fecha_cierre", "dias_resolucion"])
