"""Fábrica de código: chat donde se pide un requerimiento funcional y un agente (Claude) genera la aplicación cumpliendo las
políticas de desarrollo de FaceOps, crea el repositorio y la rama feature/… en Git y pide a FaceOps ejecutar el pipeline de Tekton
(revisión y despliegue en OpenShift, con aprobación). Se entra desde FaceOps (enlace firmado); corre en OpenShift sin root."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import base64
import threading

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session

app = Flask(__name__)
app.secret_key = os.environ.get("FABRICA_SESION") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="None")   # se usa embebida en FaceOps (otro sitio)

FACEOPS = os.environ.get("FACEOPS_URL", "").rstrip("/")                      # API (red interna)
FACEOPS_PUBLICA = os.environ.get("FACEOPS_URL_PUBLICA", FACEOPS).rstrip("/")  # enlaces para el navegador
TOKEN = os.environ.get("FACEOPS_FABRICA_TOKEN", "")
GITEA = os.environ.get("GITEA_URL", "").rstrip("/")
GITEA_PIPELINE = os.environ.get("GITEA_URL_PIPELINE", GITEA).rstrip("/")   # cómo ve Git el pipeline
GITEA_PUBLICA = os.environ.get("GITEA_URL_PUBLICA", GITEA).rstrip("/")      # enlaces para el navegador
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")
ORG = os.environ.get("GITEA_ORG", "fabrica")
DOMINIO_APPS = os.environ.get("FABRICA_DOMINIO_APPS", "")   # las apps quedan en https://<nombre>.<dominio>
MODELO = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
# modelo abierto en nuestra infraestructura (vLLM u Ollama con API compatible con OpenAI)
LOCAL_URL = os.environ.get("MODELO_LOCAL_URL", "").rstrip("/")
LOCAL_MODELO = os.environ.get("MODELO_LOCAL", "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ")
LOCAL_NOMBRE = os.environ.get("MODELO_LOCAL_NOMBRE", "Qwen2.5-Coder 14B · vLLM con GPU en nuestra infraestructura")
LOCAL_SALIDA = int(os.environ.get("MODELO_LOCAL_SALIDA", "8000"))
MOTORES = {"claude": f"Claude ({MODELO}) · Anthropic"}
if LOCAL_URL:
    MOTORES["local"] = LOCAL_NOMBRE
MOTOR_SESION = {}   # id de sesión → motor elegido
_hilo = threading.local()
VERIFY = os.environ.get("FACEOPS_VERIFY", "false") == "true"
TRABAJOS = {}    # id de trabajo → avance de una generación (pasos que ve el usuario)
PENDIENTES = {}  # id de sesión → requerimiento sobre el que el agente hizo una pregunta (la respuesta lo completa)
PROYECTOS = {}   # id de sesión → último proyecto generado (en memoria: el repositorio es la fuente de verdad)

# ------------------------------------------------------------------ acceso: enlace firmado desde FaceOps (60 s)


@app.route("/entrar")
def entrar():
    u, t, f = request.args.get("u", ""), request.args.get("t", ""), request.args.get("f", "")
    if not (TOKEN and u and t.isdigit() and abs(time.time() - int(t)) < 60):
        abort(403)
    esperada = hmac.new(TOKEN.encode(), f"{u}|{t}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperada, f):
        abort(403)
    session.clear()
    session["usuario"], session["sid"] = u[:60], secrets.token_hex(8)
    return redirect("/")


def _sesion():
    """Sesión del navegador (enlace firmado) o, si FaceOps llama de servidor a servidor, su token y el usuario."""
    tok = request.headers.get("X-Fabrica-Token", "")
    if tok and TOKEN and hmac.compare_digest(tok, TOKEN):
        u = (request.headers.get("X-Fabrica-Usuario") or "faceops")[:60]
        return u, "fo-" + hashlib.sha256(u.encode()).hexdigest()[:16]
    if not session.get("usuario"):
        abort(401)
    return session["usuario"], session["sid"]


@app.route("/salud")
def salud():
    return {"ok": True}


@app.route("/")
def inicio():
    tok = request.headers.get("X-Fabrica-Token", "")
    if not session.get("usuario") and not (tok and TOKEN and hmac.compare_digest(tok, TOKEN)):
        return render_template("fuera.html", faceops=FACEOPS_PUBLICA), 401
    usuario, _ = _sesion()
    return render_template("chat.html", usuario=usuario, faceops=FACEOPS_PUBLICA, modelo=MODELO)

# ------------------------------------------------------------------ FaceOps y políticas


def _fo(metodo, ruta, **kw):
    return requests.request(metodo, FACEOPS + ruta, headers={"X-Fabrica-Token": TOKEN}, timeout=30, verify=VERIFY, **kw)


def politicas():
    r = _fo("GET", "/api/fabrica/politicas")
    r.raise_for_status()
    return r.json()

# ------------------------------------------------------------------ agente


SISTEMA = """Eres el agente de desarrollo de la Fábrica de código de Invoke. Construyes aplicaciones completas y pequeñas a partir de un
requerimiento funcional, listas para correr en Red Hat OpenShift, y SIEMPRE cumpliendo TODAS las políticas de desarrollo que siguen
(las obligatorias sin excepción). Si el requerimiento choca con una política, lo dices y propones la alternativa que la cumple.

{politicas}

Reglas de entrega:
- Stack: Spring Boot 3 con Java 21 y Maven (pom.xml), como exigen las políticas. Sin Maven Wrapper (no incluyas mvnw ni .mvn/):
  el Containerfile compila con el comando mvn que ya trae registry.access.redhat.com/ubi9/openjdk-21. Cualquier otra pieza (base de datos, mensajería,
  caché) sale SOLO del stack tecnológico aceptado que sigue; p. ej. base relacional = la aceptada (PostgreSQL) con Spring Data JPA,
  credenciales por Secret, y en deploy/desarrollo/ su Deployment/Service (sin PVC) para el ambiente de desarrollo; en las pruebas usa H2.
{stack}
- Ambiente de desarrollo: sus dependencias (p. ej. PostgreSQL) van en deploy/desarrollo/ y usan almacenamiento efímero (emptyDir, sin PVC:
  el clúster de desarrollo no tiene almacenamiento persistente). La aplicación y su base usan el MISMO Secret «<nombre-app>-db» con las
  claves usuario, clave y base (secretKeyRef). El Secret NO va en el repositorio: fuera de producción lo crea el pipeline.
  Imagen de la base en desarrollo, exacta: {img_pg} (no inventes etiquetas). Todo ConfigMap que referencie
  un Deployment (envFrom/configMapKeyRef) debe existir con ese mismo nombre en deploy/.
- Si al requerimiento le falta algo que cambia el diseño (entidades, reglas, integraciones), NO construyas: responde en texto con hasta
  3 preguntas cortas y concretas. Si es razonable suponerlo, supónlo y dilo en el resumen.
- Sé conciso: código mínimo y funcional, sin comentarios largos ni archivos de más (idealmente 12 a 20 archivos).
- Estructura mínima: Containerfile multi-stage (construye con registry.access.redhat.com/ubi9/openjdk-21:<versión fija> y ejecuta con
  ubi9/openjdk-21-runtime:<versión fija>, USER 185, EXPOSE 8080), código en src/main, pruebas en src/test, README.md y en deploy/:
  deployment.yaml, service.yaml, route.yaml, configmap.yaml, hpa.yaml y pdb.yaml.
- deploy/deployment.yaml usa la imagen IMAGEN_APP (el pipeline la reemplaza), nombre de la app en metadata.name y en las etiquetas
  app.kubernetes.io/name, part-of: fabrica-codigo y version; sondas /actuator/health/liveness y /actuator/health/readiness en el puerto 8080; requests y limits.
- deploy/route.yaml: tls termination edge con insecureEdgeTerminationPolicy Redirect.
- Sin secretos en el código; configuración por variables de entorno.
- Trabajas en dos fases: primero el plan (herramienta planificar_proyecto: la aplicación y la lista de archivos con su propósito) y
  luego cada archivo por separado, cuando se te pida."""

PLAN = {"name": "planificar_proyecto", "description": "Plan del proyecto: qué es y la lista completa de archivos a escribir (sin su contenido).",
        "input_schema": {"type": "object", "required": ["nombre", "resumen", "stack", "politicas_aplicadas", "archivos"], "properties": {
            "nombre": {"type": "string", "description": "kebab-case, máximo 30 caracteres"},
            "resumen": {"type": "string", "description": "qué hace y qué supuestos tomaste, 2-4 frases"},
            "stack": {"type": "string"},
            "politicas_aplicadas": {"type": "array", "items": {"type": "string"}},
            "archivos": {"type": "array", "items": {"type": "object", "required": ["ruta", "proposito"], "properties": {
                "ruta": {"type": "string"}, "proposito": {"type": "string", "description": "qué contiene, clases, endpoints, nombres clave"},
                "cambia": {"type": "boolean", "description": "solo en un ajuste: true si este archivo cambia o es nuevo"}}}}}}}
PARALELO = 4
CORREGIR = {"name": "corregir_archivos", "description": "Archivos corregidos (contenido completo); lista vacía si no hay nada que corregir.",
            "input_schema": {"type": "object", "required": ["archivos"], "properties": {"archivos": {"type": "array", "items": {
                "type": "object", "required": ["ruta", "contenido"], "properties": {"ruta": {"type": "string"}, "contenido": {"type": "string"},
                                                                                  "motivo": {"type": "string"}}}}}}}


class Pregunta(Exception):
    """El agente respondió con texto (una pregunta o aclaración) en vez de entregar el plan."""


def _local(sistema, msgs, max_tokens, herramienta=None, al_texto=None):
    """El mismo pedido a un modelo abierto en nuestra infraestructura (API compatible con OpenAI, streaming)."""
    cuerpo = {"model": LOCAL_MODELO, "max_tokens": min(max_tokens, LOCAL_SALIDA), "stream": True, "temperature": 0.2,
              "messages": [{"role": "system", "content": sistema}] + msgs}
    if herramienta:
        cuerpo.update(tools=[{"type": "function", "function": {"name": herramienta["name"], "description": herramienta["description"],
                                                               "parameters": herramienta["input_schema"]}}], tool_choice="auto")
    h = {"content-type": "application/json"}
    if os.environ.get("MODELO_LOCAL_CLAVE"):
        h["authorization"] = "Bearer " + os.environ["MODELO_LOCAL_CLAVE"]
    r = requests.post(f"{LOCAL_URL}/v1/chat/completions", json=cuerpo, headers=h, timeout=900, stream=True)
    r.raise_for_status()
    js, texto, parada = "", "", ""
    for linea in r.iter_lines(decode_unicode=True):
        if not linea or not linea.startswith("data:") or linea.strip() == "data: [DONE]":
            continue
        try:
            ev = json.loads(linea[5:])
        except ValueError:
            continue
        for ch in ev.get("choices") or []:
            de = ch.get("delta") or {}
            if de.get("content"):
                texto += de["content"]
                if al_texto:
                    al_texto(len(texto))
            for tc in de.get("tool_calls") or []:
                js += ((tc.get("function") or {}).get("arguments") or "")
            if ch.get("finish_reason"):
                parada = "max_tokens" if ch["finish_reason"] == "length" else ch["finish_reason"]
    entrada = None
    if js:
        entrada = json.loads(js)
    elif herramienta and "{" in texto:   # algunos modelos abiertos escriben el JSON de la herramienta como texto
        try:
            cand = json.loads(texto[texto.index("{"):texto.rindex("}") + 1])
            if isinstance(cand, dict) and set(herramienta["input_schema"].get("required", [])) <= set(cand):
                entrada, texto = cand, ""
        except ValueError:
            pass
    return entrada, texto, parada


def _claude(sistema, msgs, max_tokens, herramienta=None, al_texto=None):
    """Una llamada al modelo elegido (Claude por defecto, streaming). Devuelve (entrada de la herramienta o None, texto, motivo de parada)."""
    if getattr(_hilo, "motor", "claude") == "local" and LOCAL_URL:
        return _local(sistema, msgs, max_tokens, herramienta, al_texto)
    cuerpo = {"model": MODELO, "max_tokens": max_tokens, "system": sistema, "messages": msgs, "stream": True}
    if herramienta:
        cuerpo.update(tools=[herramienta], tool_choice={"type": "auto"})
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=600, stream=True, json=cuerpo,
                      headers={"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    r.raise_for_status()
    js, texto, parada = "", "", ""
    for linea in r.iter_lines(decode_unicode=True):
        if not linea or not linea.startswith("data:"):
            continue
        try:
            ev = json.loads(linea[5:])
        except ValueError:
            continue
        de = ev.get("delta") or {}
        if ev.get("type") == "content_block_delta" and de.get("type") == "input_json_delta":
            js += de.get("partial_json", "")
        elif ev.get("type") == "content_block_delta" and de.get("type") == "text_delta":
            texto += de.get("text", "")
            if al_texto:
                al_texto(len(texto))
        elif ev.get("type") == "message_delta":
            parada = de.get("stop_reason") or parada
        elif ev.get("type") == "error":
            raise ValueError((ev.get("error") or {}).get("message", "error del agente"))
    return (json.loads(js) if js else None), texto, parada


def _limpiar(texto):
    """El archivo tal cual: sin el bloque ``` que a veces lo envuelve."""
    t = texto.strip("\n")
    m = re.match(r"^```[\w.+-]*\n(.*)\n```\s*$", t, re.S)
    return (m.group(1) if m else t) + "\n"


def generar(requerimiento, previo=None, correccion="", paso=lambda t: None, solo=None):
    """Fase 1: Claude planifica la aplicación y sus archivos (o pregunta). Fase 2: escribe cada archivo por separado, en paralelo.
    solo: rutas a reescribir (correcciones de la revisión previa)."""
    from concurrent.futures import ThreadPoolExecutor
    pol = politicas()
    paso(f"Leí las {len(pol['politicas'])} políticas de software vigentes (versión {pol['huella']}) y el stack aceptado")
    texto_pol = "\n".join(f"- {p['codigo']} [{p['area']} · {p['nivel']}] {p['titulo']}: {p['regla']}" for p in pol["politicas"])
    texto_stack = ("Stack tecnológico aceptado:\n" + "\n".join(f"  · {x['concepto']}: {x['nombre']} {', '.join(x['versiones'])}".rstrip()
                                                             for x in pol.get("stack") or [])) if pol.get("stack") else ""
    sistema = SISTEMA.format(politicas=texto_pol, stack=texto_stack, img_pg=IMAGENES_DEP["postgresql"])
    if solo and previo:
        plan = {k: previo[k] for k in ("nombre", "resumen", "stack", "politicas_aplicadas") if k in previo}
        plan["archivos"] = previo["plan_archivos"]
        rehacer = [a for a in plan["archivos"] if a["ruta"] in solo] or [{"ruta": r, "proposito": "corrección"} for r in solo]
    else:
        loc = getattr(_hilo, "motor", "claude") == "local"
        paso(f"{MOTORES.get('local' if loc else 'claude')} está planificando la aplicación · tiempo estimado: "
             f"{('3 a 6' if loc else '1 a 2') if previo else ('5 a 10' if loc else '2 a 4')} minutos")
        msgs = [{"role": "user", "content": f"Requerimiento funcional:\n{requerimiento}\n\nEntrega el plan con planificar_proyecto, o pregunta si falta algo esencial."}]
        if previo:
            msgs = [{"role": "user", "content": f"Proyecto actual (plan):\n{json.dumps({'nombre': previo['nombre'], 'resumen': previo.get('resumen'), 'archivos': previo['plan_archivos']}, ensure_ascii=False)}"
                     f"\n\nAjuste pedido:\n{correccion or requerimiento}\n\nEntrega el plan actualizado con planificar_proyecto, marcando cambia=true en los archivos que cambian o son nuevos."}]
        plan, texto, parada = _claude(sistema, msgs, 8000, PLAN)
        if not plan:
            if texto.strip():
                raise Pregunta(texto.strip())
            raise ValueError(f"el agente no entregó el plan (parada: {parada or 'sin datos'})")
        plan["archivos"] = [a for a in plan.get("archivos") or [] if a.get("ruta")][:40]
        if not plan["archivos"]:
            raise ValueError("el plan no trae archivos")
        rehacer = [a for a in plan["archivos"] if not previo or a.get("cambia") or a["ruta"] not in previo["archivos"]]
        paso(f"Plan listo: {plan.get('nombre')} · {len(plan['archivos'])} archivos" + (f" ({len(rehacer)} cambian)" if previo else ""))
    contenidos = dict(previo["archivos"]) if previo else {}
    lista = "\n".join(f"- {a['ruta']}: {a.get('proposito', '')}" for a in plan["archivos"])
    base = (f"Requerimiento:\n{requerimiento}\n\nPlan del proyecto «{plan.get('nombre')}» ({plan.get('stack')}):\n{plan.get('resumen')}\n\nArchivos:\n{lista}"
            + (f"\n\nCorregir:\n{correccion}" if correccion else ""))
    hechos = [0]
    ORDEN = ("domain", "model", "entity", "repository", "dto", "mapper", "exception", "service", "config", "security", "web", "controller", "api")

    def peso(r):
        rl = r.lower()
        return (next((i for i, k in enumerate(ORDEN) if f"/{k}" in rl), len(ORDEN)), r)

    motor_ = getattr(_hilo, "motor", "claude")

    def escribir(a, contexto=""):
        _hilo.motor = motor_   # los hilos del pool usan el mismo modelo que el trabajo
        previo_txt = f"\n\nContenido actual de este archivo:\n{contenidos[a['ruta']]}" if a["ruta"] in contenidos else ""
        _, txt, parada_ = _claude(sistema, [{"role": "user", "content": base + contexto + previo_txt +
                                            f"\n\nEscribe ahora el archivo {a['ruta']} ({a.get('proposito', '')}). Debe compilar con los archivos ya escritos "
                                            "(mismos nombres de clases, paquetes, constructores, métodos y campos). Responde SOLO con su contenido completo, sin explicación ni bloque ```."}], 12000)
        if parada_ == "max_tokens":
            raise ValueError(f"{a['ruta']} quedó incompleto")
        hechos[0] += 1
        paso(f"Escribí {a['ruta']} ({hechos[0]}/{len(rehacer)})")
        return a["ruta"], _limpiar(txt)

    def java_ctx(excluir=""):
        js_ = {k: v for k, v in contenidos.items() if (k.endswith(".java") or k == "pom.xml") and k != excluir}
        return ("\n\nArchivos ya escritos (úsalos tal cual):\n" + "\n".join(f"### {k}\n{v}" for k, v in sorted(js_.items()))) if js_ else ""

    principales = sorted([a for a in rehacer if a["ruta"].endswith(".java") and "/test/" not in a["ruta"]], key=lambda a: peso(a["ruta"]))
    pruebas_ = [a for a in rehacer if a["ruta"].endswith(".java") and "/test/" in a["ruta"]]
    pom_ = [a for a in rehacer if a["ruta"] == "pom.xml"]
    otros = [a for a in rehacer if a not in principales and a not in pruebas_ and a not in pom_]
    paso(f"Escribiendo {len(rehacer)} archivo{'s' if len(rehacer) != 1 else ''}: primero pom.xml y el código en orden de dependencias, el resto en paralelo")
    with ThreadPoolExecutor(PARALELO) as ex:
        resto_f = [ex.submit(escribir, a) for a in otros]   # manifiestos, README, configuración: en paralelo
        for a in pom_ + principales:                            # código principal: en orden, cada uno ve lo anterior
            r_, t_ = escribir(a, java_ctx(a["ruta"]))
            contenidos[r_] = t_
        for r_, t_ in ex.map(lambda a: escribir(a, java_ctx(a["ruta"])), pruebas_):
            contenidos[r_] = t_
        for f_ in resto_f:
            r_, t_ = f_.result()
            contenidos[r_] = t_
    if any(k.endswith(".java") for k in contenidos):
        paso("Revisando que el código compile: consistencia entre clases, constructores, métodos e imports")
        fixes, _, _ = _claude(sistema, [{"role": "user", "content": "Revisa este proyecto Spring Boot como lo haría el compilador y las pruebas: constructores y "
                                         "métodos que no existen o son privados/protegidos, firmas distintas, imports faltantes, tipos incompatibles, anotaciones "
                                         "mal usadas, pruebas que no compilan o no pasarían. Entrega con corregir_archivos SOLO los archivos que hay que cambiar "
                                         "(contenido completo); si todo está bien, entrega la lista vacía." + java_ctx()}], 16000, CORREGIR)
        cambios = [x for x in (fixes or {}).get("archivos") or [] if x.get("ruta") in contenidos]
        for x in cambios:
            contenidos[x["ruta"]] = _limpiar(x.get("contenido") or contenidos[x["ruta"]])
        paso(f"Revisión de compilación: {len(cambios)} archivo{'s' if len(cambios) != 1 else ''} corregido{'s' if len(cambios) != 1 else ''}" if cambios
             else "Revisión de compilación: sin problemas")
    validas = {a["ruta"] for a in plan["archivos"]}
    d = dict(plan, archivos={k: v for k, v in contenidos.items() if k in validas}, plan_archivos=plan["archivos"])
    d["nombre"] = re.sub(r"[^a-z0-9-]", "-", (d.get("nombre") or "app").lower()).strip("-")[:30] or "app"
    d["huella_politicas"] = pol["huella"]
    return d


def _archivos_de(malos, archivos):
    """Qué archivos rehacer según los incumplimientos de la revisión previa."""
    out = set()
    for m in malos:
        if "deploy/" in m:
            out |= {k for k in archivos if k.startswith("deploy/")}
        if "Containerfile" in m or m.startswith(("PS-01", "PS-03", "PS-08: debe", "PS-21: debe", "PS-21: falta")):
            out.add("Containerfile")
        if "pom.xml" in m or m.startswith(("PS-02", "PS-06", "PS-23")):
            out.add("pom.xml")
        mm = re.search(r"posible secreto en (\S+)", m)
        if mm:
            out.add(mm.group(1))
    return sorted(out) or sorted(archivos)


IMAGENES_DEP = {"postgresql": "registry.redhat.io/rhel9/postgresql-16:latest"}   # dependencias de desarrollo con imagen verificada


def _consistencia(archivos):
    """Coherencia entre manifiestos: ConfigMap y Secret referenciados existen o los crea el pipeline; imágenes de dependencias válidas."""
    malos = []
    yml = {k: v for k, v in archivos.items() if k.startswith("deploy/") and k.endswith((".yaml", ".yml"))}
    todo = "\n".join(yml.values())
    cms = set(re.findall(r"kind:\s*ConfigMap\s*\n(?:.*\n)*?\s*name:\s*['\"]?([\w.-]+)", todo))
    for n in set(re.findall(r"configMapRef:\s*\{?\s*\n?\s*name:\s*['\"]?([\w.-]+)", todo)) | set(re.findall(r"configMapKeyRef:\s*\{?\s*\n?\s*(?:key:.*\n\s*)?name:\s*['\"]?([\w.-]+)", todo)):
        if n not in cms:
            malos.append(f"PS-21: el Deployment usa el ConfigMap «{n}» y deploy/ no lo declara (declarados: {', '.join(sorted(cms)) or 'ninguno'})")
    svcs = set(re.findall(r"kind:\s*Service\s*\n(?:.*\n)*?\s*name:\s*['\"]?([\w.-]+)", todo))
    for ruta, cont in archivos.items():
        for host in set(re.findall(r"jdbc:\w+://([\w.-]+)", cont)):
            if host.split(".")[0] not in svcs and host not in ("localhost", "127.0.0.1"):
                malos.append(f"PS-21: {ruta} conecta a «{host}» y deploy/ no declara ese Service (declarados: {', '.join(sorted(svcs)) or 'ninguno'})")
    dadas = set(re.findall(r"-\s*name:\s*['\"]?([A-Z][A-Z0-9_]+)", todo))   # env del Deployment
    for m in re.finditer(r"kind:\s*ConfigMap[\s\S]*?\ndata:\s*\n((?:\s{2,}.*\n?)+)", todo):
        dadas |= set(re.findall(r"^\s+([A-Z][A-Z0-9_]+):", m.group(1), re.M))
    for ruta, cont in archivos.items():
        if ruta.startswith("src/main/") and re.search(r"application[\w-]*\.(ya?ml|properties)$", ruta):
            for var in set(re.findall(r"\$\{([A-Z][A-Z0-9_]+):?\}", cont)) - dadas:
                malos.append(f"PS-04: {ruta} usa ${{{var}}} y deploy/ no lo entrega (ni env del Deployment ni ConfigMap)")
    for ruta, cont in yml.items():
        for img in re.findall(r"^\s*-?\s*image:\s*['\"]?([^'\"\s]+)", cont, re.M):
            if img == "IMAGEN_APP":
                continue
            if "postgres" in img and img != IMAGENES_DEP["postgresql"]:
                malos.append(f"PS-01: {ruta} usa {img}; la imagen de PostgreSQL aceptada es {IMAGENES_DEP['postgresql']}")
        if "kind: PersistentVolumeClaim" in cont and ("desarrollo" in ruta or "/dev/" in ruta):
            malos.append(f"PS-20: {ruta} pide un PVC; en desarrollo la base usa emptyDir")
    return malos


def revisar(archivos):
    """Revisión previa de las políticas automáticas (la misma idea que el pipeline): lista de incumplimientos."""
    malos = _consistencia(archivos)
    cf = archivos.get("Containerfile") or archivos.get("Dockerfile") or ""
    if not cf:
        malos.append("PS-21: falta el Containerfile")
    froms = re.findall(r"^FROM\s+(\S+)", cf, re.M)
    if not froms or not froms[-1].startswith("registry.access.redhat.com/ubi9/openjdk-") or ":" not in froms[-1].split("/")[-1] or froms[-1].endswith(":latest"):
        malos.append("PS-01: la imagen de ejecución debe ser ubi9/openjdk-21-runtime con versión fija")
    usr = re.findall(r"^USER\s+(\S+)", cf, re.M)
    if not usr or usr[-1] in ("0", "root"):
        malos.append("PS-08: debe terminar con USER no root")
    if not re.search(r"^EXPOSE\s+8080", cf, re.M):
        malos.append("PS-21: debe exponer el puerto 8080")
    if "mvnw" in cf:
        malos.append("PS-03: el Containerfile debe usar mvn de la imagen, no ./mvnw")
    pom = archivos.get("pom.xml", "")
    if "spring-boot-starter-parent" not in pom:
        malos.append("PS-02: falta pom.xml con spring-boot-starter-parent")
    if "SNAPSHOT" in pom:
        malos.append("PS-02: dependencias SNAPSHOT")
    if "spring-boot-starter-security" not in pom:
        malos.append("PS-06: falta spring-boot-starter-security")
    if "micrometer-registry-prometheus" not in pom:
        malos.append("PS-23: falta micrometer-registry-prometheus")
    dep = "\n".join(v for k, v in archivos.items() if k.startswith("deploy/"))
    for clave, pol in (("livenessProbe", "PS-15"), ("readinessProbe", "PS-15"), ("limits", "PS-18"), ("requests", "PS-18"),
                       ("kind: Route", "PS-21"), ("termination: edge", "PS-12"), ("IMAGEN_APP", "PS-21"),
                       ("kind: HorizontalPodAutoscaler", "PS-13"), ("kind: PodDisruptionBudget", "PS-14"), ("runAsNonRoot", "PS-08")):
        if clave not in dep:
            malos.append(f"{pol}: deploy/ no tiene {clave}")
    for ruta, cont in archivos.items():
        if re.search(r"(?i)(password|passwd|secret|token|api[_-]?key)[ \t]*[:=][ \t]*['\"]?(?!\$|\{\{|<)[^'\"\s$]{6,}", cont) and not ruta.startswith("src/test/") and not ruta.endswith(".md"):   # el README documenta variables; gitleaks revisa igual
            malos.append(f"PS-07: posible secreto escrito en {ruta} (una clave o token con valor fijo; debe venir de un Secret)")
    if not any(k.startswith("src/test/") for k in archivos):
        malos.append("PS-25: faltan pruebas (src/test)")
    return sorted(set(malos))


def _trabajo(tid, sid, req_, previo):
    t = TRABAJOS[tid]
    _hilo.motor = t.get("motor", "claude")
    paso = lambda txt: t["pasos"].append({"t": round(time.time() - t["inicio"]), "texto": txt})   # noqa: E731
    try:
        p = generar(req_, previo, paso=paso)
        paso("Revisión previa de las políticas que verifica el pipeline")
        malos = revisar(p["archivos"])
        if malos:   # una corrección automática antes de mostrarlo
            paso(f"{len(malos)} incumplimiento{'s' if len(malos) != 1 else ''}: Claude los está corrigiendo")
            p2 = generar(req_, dict(p, requerimiento=req_), "\n".join(malos), paso=paso, solo=_archivos_de(malos, p["archivos"]))
            p, malos = p2, revisar(p2["archivos"])
        p["requerimiento"] = (previo or {}).get("requerimiento", req_) if previo else req_
        p["revision"] = malos
        PROYECTOS[sid] = p
        paso("Listo" if not malos else f"Listo, con {len(malos)} pendiente{'s' if len(malos) != 1 else ''}")
        t["resultado"] = {"nombre": p["nombre"], "resumen": p.get("resumen", ""), "stack": p.get("stack", ""), "politicas": p.get("politicas_aplicadas", []),
                          "archivos": dict(p["archivos"]), "revision": malos, "huella": p["huella_politicas"]}
        if t.get("origen") == "rhdh" and not malos:   # desde Developer Hub: se entrega solo, en su rama feature
            paso("Entregando en Git (rama feature) y registrando en el catálogo de Developer Hub")
            repo, rama = publicar(p, t.get("usuario", "rhdh"))
            p.update(repo=repo, rama=rama, ticket=None)
            t["resultado"]["entrega"] = {"repo": repo, "rama": rama, "url": f"{GITEA_PUBLICA}/{ORG}/{repo}/src/branch/{rama}"}
            paso(f"Entregado: {repo} · {rama}")
    except requests.RequestException as x:
        rs = getattr(x, "response", None)
        det = ""
        if rs is not None:
            try:
                det = f" {rs.status_code} · {str((rs.json().get('error') or {}).get('message', ''))[:200]}"
            except Exception:  # noqa: BLE001
                det = f" {rs.status_code}"
        app.logger.warning("generar: %s%s", type(x).__name__, det)
        t["error"] = f"No se pudo generar ({type(x).__name__}{det})"
    except Pregunta as x:
        paso("El agente necesita precisar el requerimiento")
        t["mensaje"] = str(x)[:4000]
        PENDIENTES[sid] = {"req": req_, "pregunta": t["mensaje"]}
    except (ValueError, KeyError) as x:
        app.logger.warning("generar: %s", x)
        t["error"] = f"El agente no devolvió un proyecto válido ({str(x)[:160]}); intenta de nuevo o precisa el requerimiento"
    t["listo"] = True


@app.route("/api/generar", methods=["POST"])
def api_generar():
    usuario, sid = _sesion()
    d = request.get_json(silent=True) or {}
    req_ = (d.get("mensaje") or "").strip()[:4000]
    if len(req_) < 10:
        return jsonify({"error": "Describe el requerimiento funcional"}), 400
    previo = PROYECTOS.get(sid) if d.get("ajustar") else None
    pend = PENDIENTES.pop(sid, None)
    if pend and not previo:   # respuesta a la pregunta del agente: el requerimiento se completa con ella
        req_ = f"{pend['req']}\n\nEl agente preguntó:\n{pend['pregunta']}\n\nRespuesta del usuario:\n{req_}"[:8000]
    tid = secrets.token_hex(8)
    TRABAJOS[tid] = {"sid": sid, "inicio": time.time(), "pasos": [], "listo": False, "motor": MOTOR_SESION.get(sid, "claude")}
    for k in [k for k, v in TRABAJOS.items() if time.time() - v["inicio"] > 3600]:
        TRABAJOS.pop(k, None)
    threading.Thread(target=_trabajo, args=(tid, sid, req_, previo), daemon=True).start()
    return jsonify({"trabajo": tid})


@app.route("/api/rhdh", methods=["POST"])
def api_rhdh():
    """Plantilla de Developer Hub: inicia el desarrollo; al terminar, si cumple las políticas, se entrega solo en su rama feature
    y queda registrado en el catálogo. Responde de inmediato con el trabajo para seguir su avance."""
    usuario, sid = _sesion()
    d = request.get_json(silent=True) or {}
    if d.get("usuario") and usuario == "developer-hub":   # quien lo pidió en Developer Hub (dueño del componente)
        usuario = re.sub(r"[^a-z0-9._-]", "-", str(d["usuario"]).lower())[:60]
        sid = "rhdh-" + hashlib.sha256(usuario.encode()).hexdigest()[:16]
    req_ = (d.get("mensaje") or "").strip()[:4000]
    if len(req_) < 10:
        return jsonify({"error": "Describe el requerimiento funcional"}), 400
    if d.get("nombre"):
        req_ += f"\n\nNombre del componente (usar exactamente): {re.sub(r'[^a-z0-9-]', '-', d['nombre'].lower())[:40]}"
    tid = secrets.token_hex(8)
    TRABAJOS[tid] = {"sid": sid, "inicio": time.time(), "pasos": [], "listo": False, "motor": d.get("motor") if d.get("motor") in MOTORES else "claude",
                     "origen": "rhdh", "usuario": usuario}
    threading.Thread(target=_trabajo, args=(tid, sid, req_, None), daemon=True).start()
    return jsonify({"trabajo": tid, "seguir": f"{FACEOPS_TRABAJOS}", "mensaje": "La Fábrica está generando el desarrollo; al terminar aparece en el catálogo."})


@app.route("/api/motor", methods=["GET", "POST"])
def api_motor():
    """Modelo que usa la Fábrica en esta sesión: Claude o el modelo abierto en nuestra infraestructura."""
    _, sid = _sesion()
    if request.method == "POST":
        m = (request.get_json(silent=True) or {}).get("motor", "claude")
        if m not in MOTORES:
            return jsonify({"error": "Modelo no disponible"}), 400
        MOTOR_SESION[sid] = m
    return jsonify({"motor": MOTOR_SESION.get(sid, "claude"), "motores": MOTORES})


@app.route("/api/avance/<tid>")
def api_avance(tid):
    _, sid = _sesion()
    t = TRABAJOS.get(tid)
    if not t or t["sid"] != sid:
        return jsonify({"error": "Trabajo no encontrado"}), 404
    return jsonify({"pasos": t["pasos"], "listo": t["listo"], "error": t.get("error"), "resultado": t.get("resultado"), "mensaje": t.get("mensaje"),
                    "segundos": round(time.time() - t["inicio"])})

# ------------------------------------------------------------------ Git y pipeline


def _git(metodo, ruta, **kw):
    return requests.request(metodo, f"{GITEA}/api/v1{ruta}", headers={"Authorization": f"token {GITEA_TOKEN}"}, timeout=30, **kw)


def publicar(p, usuario):
    if not _git("GET", f"/orgs/{ORG}").ok:
        _git("POST", "/orgs", json={"username": ORG, "visibility": "private"}).raise_for_status()
    repo = p["nombre"]
    if not _git("GET", f"/repos/{ORG}/{repo}").ok:
        _git("POST", f"/orgs/{ORG}/repos", json={"name": repo, "private": True, "auto_init": True, "default_branch": "main",
                                                 "description": p.get("resumen", "")[:250]}).raise_for_status()
        _git("POST", f"/repos/{ORG}/{repo}/branches", json={"new_branch_name": "develop", "old_branch_name": "main"})
    rama = "feature/" + re.sub(r"[^a-z0-9-]", "-", (p["nombre"] + "-" + time.strftime("%m%d%H%M")).lower())[:60]
    _git("POST", f"/repos/{ORG}/{repo}/branches", json={"new_branch_name": rama, "old_branch_name": "develop"}).raise_for_status()
    existentes = {}
    b_ = _git("GET", f"/repos/{ORG}/{repo}/branches/{requests.utils.quote(rama, safe='')}")
    sha_ = ((b_.json() or {}).get("commit") or {}).get("id") if b_.ok else ""
    r = _git("GET", f"/repos/{ORG}/{repo}/git/trees/{sha_}", params={"recursive": "true", "per_page": 1000}) if sha_ else None
    if r is not None and r.ok:   # los archivos que ya están (p. ej. el README inicial) se actualizan, no se crean
        existentes = {x["path"]: x["sha"] for x in r.json().get("tree", []) if x.get("type") == "blob"}
    archivos = dict(p["archivos"])
    archivos.setdefault("catalog-info.yaml", catalogo_info(p, repo, rama, usuario))
    archivos[".fabrica/conversacion.json"] = json.dumps({"repo": repo, "rama": rama, "usuario": usuario, "fecha": time.strftime("%Y-%m-%d %H:%M"),
                                                         "requerimiento": p.get("requerimiento", ""), "resumen": p.get("resumen", ""),
                                                         "conversacion": p.get("conversacion") or [{"yo": True, "texto": p.get("requerimiento", "")},
                                                                                                    {"yo": False, "texto": p.get("resumen", "")}]},
                                                        ensure_ascii=False, indent=1)
    files = [{"operation": "update" if ruta in existentes else "create", "path": ruta, "sha": existentes.get(ruta),
              "content": base64.b64encode(cont.encode()).decode()} for ruta, cont in archivos.items()]
    msg = f"feat: {p['nombre']} desde la Fábrica de código\n\n{p.get('requerimiento', '')[:900]}\n\nPolíticas de desarrollo: versión {p['huella_politicas']}\nPedido por: {usuario}"
    _git("POST", f"/repos/{ORG}/{repo}/contents", json={"branch": rama, "message": msg, "files": files}).raise_for_status()
    registrar_en_devhub(repo, rama)
    return repo, rama


RHDH_URL = os.environ.get("RHDH_URL", "").rstrip("/")         # Red Hat Developer Hub (catálogo)
RHDH_TOKEN = os.environ.get("RHDH_TOKEN", "")
DOMINIO_APPS = os.environ.get("DOMINIO_APPS", "sno.34.176.42.56.sslip.io")
FACEOPS_TRABAJOS = os.environ.get("FACEOPS_TRABAJOS", "https://faceops.34.176.42.56.sslip.io:8843/faceops/lab/fabrica/desarrollo")


def catalogo_info(p, repo, rama, usuario):
    """Componente para el catálogo de Developer Hub: dueño, fuente (rama feature), enlaces y la etiqueta que une el
    componente con sus Deployments en OpenShift (Topology)."""
    import yaml as _y
    d = {"apiVersion": "backstage.io/v1alpha1", "kind": "Component",
         "metadata": {"name": repo, "title": repo, "description": (p.get("resumen") or "")[:300],
                      "tags": ["spring-boot", "openshift", "fabrica"],
                      "annotations": {"backstage.io/kubernetes-label-selector": f"app.kubernetes.io/name={repo}",
                                      "backstage.io/kubernetes-namespace": "fabrica-dev",
                                      "backstage.io/source-location": f"url:{GITEA_PUBLICA}/{ORG}/{repo}/src/branch/{rama}",
                                      "invoke.cl/rama-feature": rama, "invoke.cl/politicas": str(p.get("huella_politicas", ""))},
                      "links": [{"url": f"{GITEA_PUBLICA}/{ORG}/{repo}/src/branch/{rama}", "title": "Repositorio (rama feature)"},
                                {"url": f"https://{repo}.{DOMINIO_APPS}", "title": "Aplicación en desarrollo"},
                                {"url": FACEOPS_TRABAJOS, "title": "Trabajos y ambientes (FaceOps)"}]},
         "spec": {"type": "service", "lifecycle": "experimental", "owner": f"user:default/{re.sub(r'[^a-z0-9._-]', '-', usuario.lower())}",
                  "system": "fabrica-de-software"}}
    return _y.safe_dump(d, allow_unicode=True, sort_keys=False)


def registrar_en_devhub(repo, rama):
    """Da de alta (o refresca) el componente en el catálogo de Developer Hub. Si no está configurado, no hace nada."""
    if not RHDH_URL or not RHDH_TOKEN:
        return None
    destino = f"{GITEA}/{ORG}/{repo}/src/branch/{rama}/catalog-info.yaml"
    try:
        r = requests.post(f"{RHDH_URL}/api/catalog/locations", json={"type": "url", "target": destino}, timeout=30, verify=False,
                          headers={"Authorization": f"Bearer {RHDH_TOKEN}"})
        if r.status_code == 409:   # ya registrado: se refresca
            requests.post(f"{RHDH_URL}/api/catalog/refresh", json={"entityRef": f"component:default/{repo}"}, timeout=30, verify=False,
                          headers={"Authorization": f"Bearer {RHDH_TOKEN}"})
        return r.status_code
    except requests.RequestException as x:
        app.logger.warning("Developer Hub: %s", x)
        return None


@app.route("/api/publicar", methods=["POST"])
def api_publicar():
    """Entrega el desarrollo: repositorio en Git (org fabrica) con main, develop y la rama feature/… con todos los archivos."""
    usuario, sid = _sesion()
    p = PROYECTOS.get(sid)
    if not p:
        return jsonify({"error": "Primero genera un proyecto"}), 400
    conv = (request.get_json(silent=True) or {}).get("conversacion")
    if isinstance(conv, list):   # la conversación del chat queda con el trabajo (se retoma desde la tabla de Trabajos)
        p["conversacion"] = [{"yo": bool(m.get("yo")), "texto": str(m.get("texto", ""))[:6000]} for m in conv[:200] if isinstance(m, dict)]
    if p["revision"]:
        return jsonify({"error": "El proyecto aún no cumple las políticas obligatorias: " + "; ".join(p["revision"])}), 409
    try:
        repo, rama = publicar(p, usuario)
    except requests.RequestException as x:
        rs = getattr(x, "response", None)
        return jsonify({"error": f"Git no aceptó el proyecto ({type(x).__name__}{' ' + str(rs.status_code) + ' · ' + rs.text[:160] if rs is not None else ''})"}), 502
    p.update(repo=repo, rama=rama, ticket=None)
    return jsonify({"repo": f"{GITEA_PUBLICA}/{ORG}/{repo}/src/branch/{rama}", "clonar": f"{GITEA_PUBLICA}/{ORG}/{repo}.git", "rama": rama})


@app.route("/api/desplegar", methods=["POST"])
def api_desplegar():
    """Despliega en desarrollo la rama feature entregada: FaceOps crea el ticket del pipeline (con aprobación) y Tekton revisa y despliega."""
    usuario, sid = _sesion()
    p = PROYECTOS.get(sid)
    if not p or not p.get("repo"):
        return jsonify({"error": "Primero entrega el repositorio (rama feature)"}), 400
    url_repo = f"{GITEA_PIPELINE}/{ORG}/{p['repo']}.git"
    r = _fo("POST", "/api/fabrica/ejecutar", json={"repo": url_repo, "rama": p["rama"], "nombre": p["repo"], "solicitante": usuario,
                                                   "requerimiento": p.get("requerimiento", "")})
    d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": f"FaceOps respondió {r.status_code}"}
    p["ticket"] = d.get("ticket")
    return jsonify({"ticket": d.get("ticket"), "error": d.get("error"), "rama": p["rama"],
                    "app_url": f"https://{p['repo']}.{DOMINIO_APPS}" if DOMINIO_APPS else ""}), (200 if r.ok else 502)


def _rama_ok(repo, rama):
    return re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", repo or "") and re.fullmatch(r"feature/[a-z0-9-]{1,80}", rama or "")


@app.route("/api/conversacion")
def api_conversacion():
    """Historia de la conversación de un trabajo (guardada con la entrega en .fabrica/conversacion.json de su rama feature)."""
    _sesion()
    repo, rama = request.args.get("repo", ""), request.args.get("rama", "")
    if not _rama_ok(repo, rama):
        return jsonify({"error": "Trabajo inválido"}), 400
    r = _git("GET", f"/repos/{ORG}/{repo}/raw/.fabrica/conversacion.json", params={"ref": rama})
    if not r.ok:
        return jsonify({"error": "Este trabajo no tiene conversación guardada (se entregó antes de que existiera esta opción)",
                        "repo": repo, "rama": rama}), 404
    return jsonify(r.json())


@app.route("/api/retomar", methods=["POST"])
def api_retomar():
    """Carga el código de la rama feature como proyecto actual de la sesión, para seguir ajustándolo desde el chat."""
    usuario, sid = _sesion()
    d = request.get_json(silent=True) or {}
    repo, rama = d.get("repo", ""), d.get("rama", "")
    if not _rama_ok(repo, rama):
        return jsonify({"error": "Trabajo inválido"}), 400
    b_ = _git("GET", f"/repos/{ORG}/{repo}/branches/{requests.utils.quote(rama, safe='')}")
    sha_ = ((b_.json() or {}).get("commit") or {}).get("id") if b_.ok else ""
    tr = _git("GET", f"/repos/{ORG}/{repo}/git/trees/{sha_}", params={"recursive": "true", "per_page": 1000}) if sha_ else None
    if tr is None or not tr.ok:
        return jsonify({"error": "No se pudo leer la rama"}), 404
    archivos = {}
    for x in tr.json().get("tree", []):
        if x.get("type") == "blob" and not x["path"].startswith(".fabrica/") and x.get("size", 0) < 200000:
            r = _git("GET", f"/repos/{ORG}/{repo}/raw/{requests.utils.quote(x['path'])}", params={"ref": rama})
            if r.ok:
                archivos[x["path"]] = r.text
    conv = _git("GET", f"/repos/{ORG}/{repo}/raw/.fabrica/conversacion.json", params={"ref": rama})
    c = conv.json() if conv.ok else {}
    PROYECTOS[sid] = {"nombre": repo, "archivos": archivos, "revision": revisar(archivos), "requerimiento": c.get("requerimiento", ""),
                      "resumen": c.get("resumen", ""), "conversacion": c.get("conversacion", []), "huella_politicas": "", "repo": repo, "rama": rama}
    return jsonify({"ok": True, "archivos": len(archivos)})


@app.route("/api/trabajos")
def api_trabajos():
    """Trabajos de la Fábrica: repositorios de la organización, su rama feature (donde vive todo lo que genera el agente) y si las
    ramas de ambiente están en la misma versión: develop (desarrollo), release/* o qa (QA) y main (producción)."""
    _sesion()
    from concurrent.futures import ThreadPoolExecutor
    en_curso = [{"inicio": round(time.time() - t["inicio"]), "paso": (t["pasos"][-1]["texto"] if t["pasos"] else "iniciando")[:140],
                 "motor": t.get("motor", "claude")} for t in TRABAJOS.values() if not t["listo"]]
    try:
        repos = _git("GET", f"/orgs/{ORG}/repos", params={"limit": 50}).json() or []
    except (requests.RequestException, ValueError):
        return jsonify({"error": "Git no responde", "repos": [], "en_curso": en_curso})

    def uno(r):
        n = r["name"]
        try:
            ramas = {b["name"]: b for b in (_git("GET", f"/repos/{ORG}/{n}/branches", params={"limit": 100}).json() or [])}
        except (requests.RequestException, ValueError):
            ramas = {}
        sha = lambda b: ((ramas.get(b) or {}).get("commit") or {}).get("id", "")   # noqa: E731
        qa = "qa" if "qa" in ramas else max((b for b in ramas if b.startswith("release/")), default="")

        def adelante(base, cabeza):   # commits de «cabeza» que no están en «base»
            if not base or not cabeza or base not in ramas or cabeza not in ramas:
                return None
            try:
                c = _git("GET", f"/repos/{ORG}/{n}/compare/{requests.utils.quote(base, safe='')}...{requests.utils.quote(cabeza, safe='')}").json()
                return c.get("total_commits", len(c.get("commits") or []))
            except (requests.RequestException, ValueError):
                return None
        def titulo(rama):
            try:
                c = _git("GET", f"/repos/{ORG}/{n}/raw/.fabrica/conversacion.json", params={"ref": rama})
                if c.ok:
                    j = c.json()
                    return {"titulo": (j.get("requerimiento") or "").strip().splitlines()[0][:110] if j.get("requerimiento") else n,
                            "usuario": j.get("usuario", ""), "fecha": j.get("fecha", ""), "conversacion": True}
            except (requests.RequestException, ValueError):
                pass
            return {"titulo": (r.get("description") or n)[:110], "conversacion": False}
        features = []
        for b in sorted((b for b in ramas if b.startswith("feature/")), key=lambda b: ramas[b]["commit"].get("timestamp", ""), reverse=True)[:5]:
            c = ramas[b]["commit"]
            features.append({**titulo(b), "rama": b, "sha": c.get("id", "")[:7], "fecha": c.get("timestamp", "")[:16].replace("T", " "),
                             "mensaje": (c.get("message") or "").splitlines()[0][:90], "por_integrar": adelante("develop", b),
                             "url": f"{GITEA_PUBLICA}/{ORG}/{n}/src/branch/{b}"})
        amb = {"dev": "develop" if "develop" in ramas else "", "qa": qa, "prod": "main" if "main" in ramas else ("master" if "master" in ramas else "")}
        shas = {k: sha(v)[:7] for k, v in amb.items() if v}
        fuera = [b for b in ramas if not (b.startswith(("feature/", "release/", "hotfix/")) or b in ("develop", "main", "master", "qa"))]
        return {"repo": n, "url": f"{GITEA_PUBLICA}/{ORG}/{n}", "features": features, "ramas": amb, "shas": shas,
                "misma_version": len(set(shas.values())) == 1 and len(shas) >= 2, "sin_qa": not amb["qa"],
                "dev_vs_prod": adelante(amb["prod"], amb["dev"]), "qa_vs_prod": adelante(amb["prod"], amb["qa"]) if amb["qa"] else None,
                "ramas_fuera_de_gitflow": fuera, "actualizado": (r.get("updated_at") or "")[:16].replace("T", " ")}
    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(uno, sorted(repos, key=lambda r: r.get("updated_at") or "", reverse=True)))
    return jsonify({"repos": out, "en_curso": en_curso})


@app.route("/api/ticket/<numero>")
def api_ticket(numero):
    _sesion()
    r = _fo("GET", f"/api/fabrica/ticket/{numero}")
    return (r.json(), r.status_code) if r.ok else ({"error": f"FaceOps respondió {r.status_code}"}, 502)
