"""FaceAdmin · pulsos de uso de FaceOps. Cada instancia de FaceOps envía por lotes (con su llave) un «pulso» por request y por
evento de negocio; aquí se validan, se limpian de nuevo y se guardan en el data lake de FaceAdmin:

    s3://faceops-pulsos/instancia=<código>/fecha=YYYY-MM-DD/hora=HH/<lote>.ndjson.gz      (fecha y hora en UTC)

El data lake es un MinIO en la misma VM (FACEADMIN_S3_URL, FACEADMIN_S3_ACCESS, FACEADMIN_S3_SECRET, en /etc/faceadmin/s3.env);
sin esas variables se usa la misma estructura en disco (/var/lib/faceadmin/lake). La vista «Uso de FaceOps» lee el lake y guarda un
agregado por instancia y día (se recalcula solo si cambiaron los objetos del día). Esquema del pulso: faceadmin/docs/pulsos.md."""
import gzip
import hashlib
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import abort, render_template, request

from app import BD, app, ej, en_alcance, q, q1, requiere_login, solo_invoke

BUCKET = os.environ.get("FACEADMIN_PULSOS_BUCKET", "faceops-pulsos")
RETENCION = int(os.environ.get("FACEADMIN_PULSOS_RETENCION_DIAS", "180"))
MAX_GZ, MAX_RAW, MAX_LINEAS = 8 * 1024 * 1024, 64 * 1024 * 1024, 50000
SENSIBLE = re.compile(r"(clave|pass|pwd|token|secret|key|llave|auth|cookie|csrf|otp|totp|firma|signature|credential|bearer|correo|email)", re.I)
CAMPOS = {"v", "ts", "instancia", "instancia_id", "tipo", "evento", "datos", "cliente", "usuario_id", "rol", "ver_como", "metodo", "ruta", "ruta_real",
          "endpoint", "status", "ms", "bytes_resp", "bytes_req", "ua", "ip", "sesion", "xhr", "modulo", "seccion", "error"}
BORDES = [1, 2, 5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 20000, 60000]
_RX_SECRETO = [(re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9\-\._~\+/=]+"), r"\1 [redactado]"),
               (re.compile(r"(?i)((?:clave|password|passwd|pwd|token|secret|api[_-]?key|llave|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+"), r"\1[redactado]"),
               (re.compile(r"[\w\.\-+]+@[\w\-]+\.[\w\.\-]+"), "[correo]")]


# ----------------------------------------------------------------- almacén (MinIO / S3 o disco)
class _Disco:
    def __init__(self, base):
        self.base = base

    def _p(self, k):
        return os.path.join(self.base, BUCKET, k)

    def put(self, k, datos):
        p = self._p(k)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "wb") as f:
            f.write(datos)
        os.replace(p + ".tmp", p)

    def listar(self, pref):
        raiz, out = self._p(""), []
        for d, _, fs in os.walk(self._p(pref) if os.path.isdir(self._p(pref)) else os.path.dirname(self._p(pref))):
            for f in fs:
                k = os.path.relpath(os.path.join(d, f), raiz)
                if k.startswith(pref) and not f.endswith(".tmp"):
                    out.append((k, os.path.getsize(os.path.join(d, f))))
        return sorted(out)

    def get(self, k):
        return open(self._p(k), "rb").read()

    def borrar(self, ks):
        for k in ks:
            try:
                os.remove(self._p(k))
            except OSError:
                pass


class _S3:
    def __init__(self, url, acc, sec):
        import boto3
        from botocore.config import Config
        self.c = boto3.client("s3", endpoint_url=url, aws_access_key_id=acc, aws_secret_access_key=sec, region_name="us-east-1",
                              config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 3},
                                            connect_timeout=5, read_timeout=30))
        try:
            self.c.head_bucket(Bucket=BUCKET)
        except Exception:  # noqa: BLE001
            self.c.create_bucket(Bucket=BUCKET)
        try:   # retención en el propio lake (además de la purga diaria de FaceAdmin)
            self.c.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration={"Rules": [
                {"ID": "retencion", "Status": "Enabled", "Filter": {"Prefix": "instancia="}, "Expiration": {"Days": RETENCION}}]})
        except Exception:  # noqa: BLE001
            pass

    def put(self, k, datos):
        self.c.put_object(Bucket=BUCKET, Key=k, Body=datos, ContentType="application/x-ndjson", ContentEncoding="gzip")

    def listar(self, pref):
        out = []
        for pg in self.c.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=pref):
            out += [(o["Key"], o["Size"]) for o in pg.get("Contents", [])]
        return out

    def get(self, k):
        return self.c.get_object(Bucket=BUCKET, Key=k)["Body"].read()

    def borrar(self, ks):
        ks = list(ks)
        for i in range(0, len(ks), 1000):
            self.c.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": k} for k in ks[i:i + 1000]], "Quiet": True})


_alm = {"o": None}


def almacen():
    if _alm["o"] is None:
        url = os.environ.get("FACEADMIN_S3_URL", "")
        if url and os.environ.get("FACEADMIN_S3_ACCESS") and os.environ.get("FACEADMIN_S3_SECRET"):
            _alm["o"] = _S3(url, os.environ["FACEADMIN_S3_ACCESS"], os.environ["FACEADMIN_S3_SECRET"])
            _alm["tipo"] = f"MinIO · s3://{BUCKET}"
        else:
            _alm["o"] = _Disco(os.path.join(os.path.dirname(BD), "lake"))
            _alm["tipo"] = f"disco · {os.path.join(os.path.dirname(BD), 'lake', BUCKET)}"
    return _alm["o"]


def iniciar():
    with app.app_context():
        ej("CREATE TABLE IF NOT EXISTS pulsos_dia (codigo TEXT, fecha TEXT, firma TEXT, calculado TEXT, datos TEXT, PRIMARY KEY (codigo, fecha))")
        ej("CREATE TABLE IF NOT EXISTS pulsos_lotes (codigo TEXT, fecha TEXT, lotes INTEGER DEFAULT 0, pulsos INTEGER DEFAULT 0, ultimo TEXT, "
           "PRIMARY KEY (codigo, fecha))")


iniciar()


# ----------------------------------------------------------------- ingesta
def _redactar(t, n=300):
    t = str(t or "")
    for rx, rep in _RX_SECRETO:
        t = rx.sub(rep, t)
    return t[:n]


def _limpiar(d, cod):
    """Deja solo los campos del esquema, con tipos y largos acotados; FaceAdmin no confía en que FaceOps ya redactó."""
    p = {k: v for k, v in d.items() if k in CAMPOS}
    p["instancia_faceops"] = str(p.get("instancia") or "")[:60]
    p["instancia"] = cod
    for k in ("ruta", "ruta_real", "endpoint", "ua", "ip", "modulo", "seccion", "cliente", "rol", "sesion", "metodo", "tipo", "evento", "instancia_id"):
        if k in p:
            p[k] = _redactar(p[k], 300) if k == "ruta_real" else str(p[k])[:200]
    for k in ("status", "bytes_resp", "bytes_req", "usuario_id", "v"):
        if k in p and not isinstance(p[k], int):
            try:
                p[k] = int(p[k])
            except (TypeError, ValueError):
                p.pop(k)
    if "ms" in p:
        try:
            p["ms"] = float(p["ms"])
        except (TypeError, ValueError):
            p.pop("ms")
    if isinstance(p.get("datos"), dict):
        p["datos"] = {str(k)[:40]: (v if isinstance(v, (int, float, bool)) or v is None else _redactar(v)) for k, v in list(p["datos"].items())[:30]
                      if not SENSIBLE.search(str(k))}
    else:
        p.pop("datos", None)
    if isinstance(p.get("error"), dict):
        e = p["error"]
        p["error"] = {"tipo": str(e.get("tipo", ""))[:80], "mensaje": _redactar(e.get("mensaje"), 400),
                      "traza": [str(x)[:160] for x in (e.get("traza") or [])[:12]] if isinstance(e.get("traza"), list) else []}
    else:
        p.pop("error", None)
    return p


@app.route("/api/instancia/pulsos", methods=["POST"])
def api_pulsos():
    """FaceOps envía sus pulsos (NDJSON, gzip) con su llave de instancia. Idempotente por lote: reenviar el mismo lote lo sobrescribe."""
    tok = request.headers.get("X-FaceAdmin-Token", "")
    b = q1("SELECT * FROM bloqueos WHERE token=?", hashlib.sha256(tok.encode()).hexdigest()) if tok else None
    if not b:
        return {"error": "no autorizado"}, 401
    c = q1("SELECT * FROM clientes WHERE id=?", b["cliente_id"]) or {}
    cod = re.sub(r"[^a-z0-9_\-]", "", (c.get("codigo") or "").lower())[:40]
    if not cod:
        return {"error": "cliente sin código"}, 409
    if (request.content_length or 0) > MAX_GZ:
        return {"error": "lote demasiado grande"}, 413
    crudo = request.get_data(cache=False)
    try:
        if request.headers.get("Content-Encoding") == "gzip" or crudo[:2] == b"\x1f\x8b":
            crudo = gzip.GzipFile(fileobj=io.BytesIO(crudo)).read(MAX_RAW + 1)
    except (OSError, EOFError):
        return {"error": "gzip inválido"}, 400
    if len(crudo) > MAX_RAW:
        return {"error": "lote demasiado grande"}, 413
    lote = re.sub(r"[^A-Za-z0-9\-]", "", request.headers.get("X-Pulsos-Lote", ""))[:60] or hashlib.sha256(crudo).hexdigest()[:20]
    grupos, malos = {}, 0
    for i, ln in enumerate(crudo.splitlines()):
        if i >= MAX_LINEAS:
            malos += 1
            continue
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
            ts = datetime.strptime(str(d["ts"])[:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, KeyError, TypeError):
            malos += 1
            continue
        if not isinstance(d, dict):
            malos += 1
            continue
        p = _limpiar(d, cod)
        p["recibido"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        grupos.setdefault((ts.strftime("%Y-%m-%d"), ts.strftime("%H")), []).append(json.dumps(p, ensure_ascii=False, separators=(",", ":")))
    try:
        alm = almacen()
        for (f, h), filas in grupos.items():
            alm.put(f"instancia={cod}/fecha={f}/hora={h}/{lote}.ndjson.gz", gzip.compress(("\n".join(filas) + "\n").encode(), 6))
    except Exception as e:  # noqa: BLE001  el lake no responde: FaceOps reintenta (conserva su cola)
        app.logger.warning("pulsos: lake no disponible: %s", type(e).__name__)
        return {"error": "data lake no disponible"}, 503
    for (f, _h), filas in grupos.items():
        ej("INSERT INTO pulsos_lotes (codigo, fecha, lotes, pulsos, ultimo) VALUES (?,?,1,?,?) ON CONFLICT(codigo, fecha) DO UPDATE SET "
           "lotes=lotes+1, pulsos=pulsos+excluded.pulsos, ultimo=excluded.ultimo", cod, f, len(filas), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _purgar_si_toca()
    return {"ok": True, "aceptados": sum(len(x) for x in grupos.values()), "rechazados": malos, "lote": lote}


_purga = {"dia": ""}


def _purgar_si_toca():
    """Una vez al día, en segundo plano: borra del lake lo que pasó la retención (FACEADMIN_PULSOS_RETENCION_DIAS)."""
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _purga["dia"] == hoy:
        return
    _purga["dia"] = hoy
    corte = (datetime.now(timezone.utc) - timedelta(days=RETENCION)).strftime("%Y-%m-%d")

    def _p():
        try:
            alm = almacen()
            viejos = [k for k, _ in alm.listar("instancia=") if (m := re.search(r"/fecha=(\d{4}-\d{2}-\d{2})/", k)) and m.group(1) < corte]
            if viejos:
                alm.borrar(viejos)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_p, daemon=True).start()


# ----------------------------------------------------------------- agregados (leídos del lake)
def _cubo(ms):
    for i, b in enumerate(BORDES):
        if ms <= b:
            return i
    return len(BORDES)


def percentil(hist, p):
    tot = sum(hist)
    if not tot:
        return None
    obj, acc = tot * p, 0
    for i, n in enumerate(hist):
        if acc + n >= obj and n:
            lo = BORDES[i - 1] if i else 0
            hi = BORDES[i] if i < len(BORDES) else BORDES[-1] * 2
            return round(lo + (hi - lo) * (obj - acc) / n, 1)
        acc += n
    return None


def _vacio():
    return {"n": 0, "req": 0, "e5": 0, "e4": 0, "eventos": {}, "horas": {}, "rutas": {}, "hist": [0] * (len(BORDES) + 1), "usuarios": {},
            "sesiones": [], "errores": [], "modulos": {}}


def agregado_dia(cod, fecha):
    """Agregado de una instancia en un día UTC, leído del lake. Se guarda con la firma (objetos y bytes) y se recalcula si cambió."""
    alm = almacen()
    objs = alm.listar(f"instancia={cod}/fecha={fecha}/")
    firma = f"{len(objs)}:{sum(s for _, s in objs)}"
    fila = q1("SELECT * FROM pulsos_dia WHERE codigo=? AND fecha=?", cod, fecha)
    if fila and fila["firma"] == firma:
        return json.loads(fila["datos"])
    a, ses = _vacio(), set()
    vistos = set()   # un pulso reenviado en dos lotes distintos cuenta una vez
    for k, _ in sorted(objs):
        try:
            txt = gzip.decompress(alm.get(k)).decode()
        except Exception:  # noqa: BLE001
            continue
        for ln in txt.splitlines():
            try:
                p = json.loads(ln)
            except ValueError:
                continue
            h = hashlib.sha1(ln.split(',"recibido"')[0].encode()).hexdigest()
            if h in vistos:
                continue
            vistos.add(h)
            a["n"] += 1
            hh = p.get("ts", "")[11:13]
            hr = a["horas"].setdefault(hh, {"n": 0, "err": 0})
            if p.get("usuario_id") is not None:
                u = a["usuarios"].setdefault(str(p["usuario_id"]), {"n": 0, "rol": p.get("rol", ""), "ultimo": ""})
                u["n"] += 1
                u["ultimo"] = max(u["ultimo"], p.get("ts", ""))
            if p.get("tipo") == "evento":
                a["eventos"][p.get("evento", "?")] = a["eventos"].get(p.get("evento", "?"), 0) + 1
                if p.get("evento") == "error_aap":
                    a["errores"].append({"ts": p.get("ts"), "ruta": "(evento)", "status": "", "tipo": "error_aap",
                                         "mensaje": (p.get("datos") or {}).get("mensaje", ""), "traza": [], "usuario_id": p.get("usuario_id")})
                continue
            a["req"] += 1
            hr["n"] += 1
            st = int(p.get("status") or 0)
            ms = float(p.get("ms") or 0)
            if p.get("sesion"):
                ses.add(p["sesion"])
            a["modulos"][p.get("modulo") or "?"] = a["modulos"].get(p.get("modulo") or "?", 0) + 1
            r = a["rutas"].setdefault(f"{p.get('metodo', '')} {p.get('ruta', '')}", {"n": 0, "err": 0, "hist": [0] * (len(BORDES) + 1), "ms": 0.0})
            r["n"] += 1
            r["ms"] += ms
            r["hist"][_cubo(ms)] += 1
            a["hist"][_cubo(ms)] += 1
            if st >= 500 or (p.get("error") and st >= 500):
                a["e5"] += 1
                r["err"] += 1
                hr["err"] += 1
            elif st >= 400:
                a["e4"] += 1
            if st >= 500 or (st >= 400 and st not in (401, 403, 404, 302)):
                e = p.get("error") or {}
                a["errores"].append({"ts": p.get("ts"), "ruta": p.get("ruta"), "ruta_real": p.get("ruta_real"), "status": st, "tipo": e.get("tipo", ""),
                                     "mensaje": e.get("mensaje", ""), "traza": e.get("traza") or [], "usuario_id": p.get("usuario_id"),
                                     "metodo": p.get("metodo")})
    a["sesiones"] = sorted(ses)
    a["errores"] = sorted(a["errores"], key=lambda x: x.get("ts") or "")[-200:]
    if len(a["rutas"]) > 300:
        a["rutas"] = dict(sorted(a["rutas"].items(), key=lambda kv: -kv[1]["n"])[:300])
    ej("INSERT INTO pulsos_dia (codigo, fecha, firma, calculado, datos) VALUES (?,?,?,?,?) ON CONFLICT(codigo, fecha) DO UPDATE SET "
       "firma=excluded.firma, calculado=excluded.calculado, datos=excluded.datos", cod, fecha, firma, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
       json.dumps(a, separators=(",", ":")))
    return a


def resumen(cod, desde, hasta):
    d0, d1 = datetime.strptime(desde, "%Y-%m-%d"), datetime.strptime(hasta, "%Y-%m-%d")
    dias = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)][:62]
    t = _vacio()
    serie, ses = [], set()
    for f in dias:
        a = agregado_dia(cod, f)
        for k in ("n", "req", "e5", "e4"):
            t[k] += a[k]
        for k, v in a["eventos"].items():
            t["eventos"][k] = t["eventos"].get(k, 0) + v
        for k, v in a["modulos"].items():
            t["modulos"][k] = t["modulos"].get(k, 0) + v
        t["hist"] = [x + y for x, y in zip(t["hist"], a["hist"])]
        for k, r in a["rutas"].items():
            x = t["rutas"].setdefault(k, {"n": 0, "err": 0, "hist": [0] * (len(BORDES) + 1), "ms": 0.0})
            x["n"] += r["n"]
            x["err"] += r["err"]
            x["ms"] += r["ms"]
            x["hist"] = [p + q_ for p, q_ in zip(x["hist"], r["hist"])]
        for k, u in a["usuarios"].items():
            x = t["usuarios"].setdefault(k, {"n": 0, "rol": u["rol"], "ultimo": ""})
            x["n"] += u["n"]
            x["ultimo"] = max(x["ultimo"], u["ultimo"])
        ses |= set(a["sesiones"])
        t["errores"] += a["errores"]
        for h in range(24):
            hh = f"{h:02d}"
            v = a["horas"].get(hh, {"n": 0, "err": 0})
            serie.append({"etq": f"{f[5:]} {hh}h", "n": v["n"], "err": v["err"]})
    if len(dias) > 3:   # rangos largos: un punto por día
        serie = [{"etq": f[5:], "n": sum(s["n"] for s in serie[i * 24:(i + 1) * 24]), "err": sum(s["err"] for s in serie[i * 24:(i + 1) * 24])}
                 for i, f in enumerate(dias)]
    rutas = sorted(({"ruta": k, "n": r["n"], "err": r["err"], "prom": round(r["ms"] / r["n"], 1) if r["n"] else 0, "p50": percentil(r["hist"], .5),
                     "p95": percentil(r["hist"], .95)} for k, r in t["rutas"].items()), key=lambda x: -x["n"])[:25]
    return {"t": t, "serie": serie, "rutas": rutas, "p50": percentil(t["hist"], .5), "p95": percentil(t["hist"], .95), "sesiones": len(ses),
            "errores": sorted(t["errores"], key=lambda x: x.get("ts") or "", reverse=True)[:100], "dias": dias,
            "usuarios": sorted(({"id": k, **u} for k, u in t["usuarios"].items()), key=lambda x: -x["n"])}


@app.route("/fabrica/uso-faceops")
@requiere_login
def uso_faceops():
    """Uso de FaceOps por instancia: requests por hora, rutas más usadas, errores, latencia y usuarios activos (desde el data lake)."""
    solo_invoke()
    insts = [c for c in q("SELECT c.* FROM clientes c JOIN bloqueos b ON b.cliente_id=c.id WHERE COALESCE(b.token,'')<>'' ORDER BY c.nombre")
             if en_alcance(c)]
    llegan = {r["codigo"]: r for r in q("SELECT codigo, SUM(pulsos) pulsos, MAX(ultimo) ultimo FROM pulsos_lotes GROUP BY codigo")}
    cod = request.args.get("inst") or next((c["codigo"] for c in insts if c["codigo"] in llegan), insts[0]["codigo"] if insts else "")
    if insts and cod not in {c["codigo"] for c in insts}:
        abort(404)
    hoy = datetime.now(timezone.utc).date()
    try:
        hasta = datetime.strptime(request.args.get("hasta") or hoy.isoformat(), "%Y-%m-%d").date()
        desde = datetime.strptime(request.args.get("desde") or (hasta - timedelta(days=0 if request.args.get("hasta") else 1)).isoformat(), "%Y-%m-%d").date()
    except ValueError:
        abort(400)
    if desde > hasta:
        desde, hasta = hasta, desde
    desde = max(desde, hasta - timedelta(days=61))
    r, error = None, ""
    if cod:
        try:
            r = resumen(cod, desde.isoformat(), hasta.isoformat())
        except Exception as e:  # noqa: BLE001
            error = f"No se pudo leer el data lake ({type(e).__name__})"
    almacen()
    return render_template("uso_faceops.html", insts=insts, cod=cod, desde=desde.isoformat(), hasta=hasta.isoformat(), r=r, error=error,
                           llegan=llegan, lake=_alm.get("tipo", ""), retencion=RETENCION, bucket=BUCKET)
