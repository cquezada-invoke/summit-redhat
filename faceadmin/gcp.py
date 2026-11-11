"""FaceAdmin · cliente mínimo de la API REST de Compute Engine para las Demos instantáneas en GCP.

- La clave de la cuenta de servicio (JSON) la carga el usuario en FaceAdmin (Demos y PoC › Demo instantánea en GCP). Se guarda CIFRADA
  (Fernet) en un archivo con permisos 600 junto a la base de datos; la llave de cifrado vive en otro archivo 600. Nunca va a la BD,
  al repositorio ni a los logs: de ella solo se muestran el proyecto y el correo de la cuenta.
- Solo opera instancias cuyo nombre parte con el prefijo de demos y que llevan la etiqueta faceadmin-demo=si (se verifica antes de
  encender, apagar o eliminar). En IAM se recomienda además una condición por nombre (ver herramientas/demo-gcp/README).
- El transporte HTTP es inyectable (Compute(sesion=...)) para las pruebas con la API simulada."""
import json
import os
import re
import time

import requests

from app import BD

DIR = os.path.dirname(BD)
SA_ARCH = os.path.join(DIR, "gcp_sa.enc")          # JSON de la cuenta de servicio, cifrado
LLAVE_ARCH = os.path.join(DIR, "gcp_sa.llave")     # llave Fernet (600)
CFG_ARCH = os.path.join(DIR, "demo_gcp.json")      # configuración no secreta
API = "https://compute.googleapis.com/compute/v1"
ALCANCE = "https://www.googleapis.com/auth/compute"
ETIQUETA = "faceadmin-demo"
CFG_DEF = {"proyecto": "", "zona": "southamerica-west1-a", "imagen": "familia:faceops-demo", "tipo": "n2-standard-8", "disco_gb": 100,
           "red": "default", "prefijo": "demo-", "faceadmin_url": "", "inactividad_min": 30, "dias_max": 7, "aviso_horas": 24,
           "tz": "America/Santiago", "dias_habiles": "1-5", "hora_ini": 8, "hora_fin": 19, "arranque_programado": False}


class ErrorGCP(Exception):
    pass


def _escribir_600(ruta, datos):
    with open(os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
        fh.write(datos)
    os.chmod(ruta, 0o600)


def config():
    d = dict(CFG_DEF)
    try:
        x = json.load(open(CFG_ARCH, encoding="utf-8"))
        d.update({k: x[k] for k in CFG_DEF if k in x})
    except (OSError, ValueError):
        pass
    return d


def guardar_config(nuevo):
    d = config()
    d.update({k: v for k, v in nuevo.items() if k in CFG_DEF})
    _escribir_600(CFG_ARCH, json.dumps(d, ensure_ascii=False).encode())
    return d


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise ErrorGCP("Falta la biblioteca cryptography en FaceAdmin") from e
    if not os.path.exists(LLAVE_ARCH):
        _escribir_600(LLAVE_ARCH, Fernet.generate_key())
    return Fernet(open(LLAVE_ARCH, "rb").read().strip())


def cifrar(texto):
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(token):
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ErrorGCP("No se pudo descifrar el dato (¿cambió la llave?)") from e


def validar_sa(texto):
    """Valida el JSON de la cuenta de servicio sin mostrar su contenido. Devuelve (proyecto, correo)."""
    try:
        d = json.loads(texto)
    except ValueError as e:
        raise ErrorGCP("El archivo no es un JSON válido") from e
    if d.get("type") != "service_account" or not d.get("private_key") or not d.get("client_email"):
        raise ErrorGCP("El JSON no es una clave de cuenta de servicio de Google (type=service_account con private_key)")
    return d.get("project_id", ""), d["client_email"]


def guardar_sa(texto):
    proyecto, correo = validar_sa(texto)
    _escribir_600(SA_ARCH, _fernet().encrypt(texto.encode()))
    if proyecto and not config().get("proyecto"):
        guardar_config({"proyecto": proyecto})
    return proyecto, correo


def borrar_sa():
    for r in (SA_ARCH,):
        if os.path.exists(r):
            os.remove(r)


def sa_resumen():
    """Solo lo mostrable: proyecto y correo de la cuenta (nunca la clave)."""
    if not os.path.exists(SA_ARCH):
        return None
    try:
        d = json.loads(_fernet().decrypt(open(SA_ARCH, "rb").read()).decode())
        return {"proyecto": d.get("project_id", ""), "correo": d.get("client_email", ""), "id_clave": (d.get("private_key_id") or "")[:8]}
    except Exception:  # noqa: BLE001
        return {"proyecto": "", "correo": "", "error": "no se pudo leer la clave guardada"}


def _sa_info():
    if not os.path.exists(SA_ARCH):
        raise ErrorGCP("Falta cargar la clave de la cuenta de servicio de GCP en la configuración")
    return json.loads(_fernet().decrypt(open(SA_ARCH, "rb").read()).decode())


def nombre_valido(n):
    return bool(re.fullmatch(r"[a-z]([-a-z0-9]{0,61}[a-z0-9])?", n or ""))


def etiqueta(v):
    """Valor válido para una etiqueta de GCP: minúsculas, dígitos, - y _ (máx. 63)."""
    return re.sub(r"[^a-z0-9_-]", "-", str(v).lower())[:63]


class Compute:
    def __init__(self, cfg=None, sesion=None, token=None):
        self.cfg = cfg or config()
        self.p, self.z = self.cfg["proyecto"], self.cfg["zona"]
        self.region = self.z.rsplit("-", 1)[0]
        self.s = sesion or requests.Session()
        self._token, self._vence = token, (time.time() + 3000 if token else 0)
        if not self.p or not self.z:
            raise ErrorGCP("Falta el proyecto o la zona de GCP en la configuración")

    # --- autenticación (google-auth con la cuenta de servicio cargada)
    def _tok(self):
        if self._token and time.time() < self._vence:
            return self._token
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as e:
            raise ErrorGCP("Falta la biblioteca google-auth en FaceAdmin") from e
        cred = service_account.Credentials.from_service_account_info(_sa_info(), scopes=[ALCANCE])
        try:
            cred.refresh(Request())
        except Exception as e:  # noqa: BLE001  nunca se muestra la clave, solo el tipo de error
            raise ErrorGCP(f"Google rechazó la cuenta de servicio ({type(e).__name__})") from e
        self._token, self._vence = cred.token, time.time() + 3000
        return self._token

    def _r(self, metodo, ruta, **kw):
        url = ruta if ruta.startswith("http") else f"{API}/projects/{self.p}/{ruta.lstrip('/')}"
        try:
            r = self.s.request(metodo, url, headers={"Authorization": f"Bearer {self._tok()}"}, timeout=40, **kw)
        except requests.RequestException as e:
            raise ErrorGCP(f"GCP no responde ({type(e).__name__})") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            try:
                m = r.json().get("error", {}).get("message", "")
            except ValueError:
                m = r.text[:200]
            raise ErrorGCP(f"GCP {metodo} → {r.status_code}: {m[:300]}")
        return r.json() if r.content else {}

    # --- consultas
    def probar(self):
        """Pasos de verificación: token, zona, imagen, tipo de máquina y permiso de listar instancias de demo."""
        pasos = []
        try:
            self._tok()
            pasos.append(("Cuenta de servicio aceptada por Google", True, ""))
        except ErrorGCP as e:
            return [("Cuenta de servicio aceptada por Google", False, str(e))]
        for titulo, fn in ((f"Zona {self.z}", lambda: self._r("GET", f"zones/{self.z}")),
                           (f"Tipo de máquina {self.cfg['tipo']}", lambda: self._r("GET", f"zones/{self.z}/machineTypes/{self.cfg['tipo']}")),
                           (f"Imagen {self.cfg['imagen']}", lambda: self._r("GET", self.url_imagen())),
                           ("Listar instancias de demo", lambda: self.instancias() is not None or True)):
            try:
                ok = fn() is not None
                pasos.append((titulo, ok, "" if ok else "no existe o sin permiso"))
            except ErrorGCP as e:
                pasos.append((titulo, False, str(e)))
        return pasos

    def url_imagen(self):
        im = self.cfg["imagen"]
        if im.startswith("familia:"):
            return f"global/images/family/{im.split(':', 1)[1]}"
        if im.startswith("projects/"):
            return f"{API}/{im}"
        return f"global/images/{im}"

    def instancias(self):
        r = self._r("GET", f"zones/{self.z}/instances", params={"filter": f"labels.{ETIQUETA}=si"}) or {}
        return r.get("items", [])

    def instancia(self, nombre):
        return self._r("GET", f"zones/{self.z}/instances/{nombre}")

    def _propia(self, nombre):
        """Solo se tocan instancias de demo: prefijo + etiqueta faceadmin-demo=si."""
        if not nombre_valido(nombre) or not nombre.startswith(self.cfg["prefijo"]):
            raise ErrorGCP(f"La instancia {nombre} no es una demo de FaceAdmin (prefijo)")
        i = self.instancia(nombre)
        if i is not None and (i.get("labels") or {}).get(ETIQUETA) != "si":
            raise ErrorGCP(f"La instancia {nombre} no lleva la etiqueta {ETIQUETA}=si: FaceAdmin no la toca")
        return i

    def operacion(self, op):
        if not op:
            return {}
        if op.get("zone"):
            return self._r("GET", f"zones/{self.z}/operations/{op['name']}") or {}
        return self._r("GET", f"regions/{self.region}/operations/{op['name']}") or {}

    # --- IP estática (la URL sslip.io no cambia al apagar y encender)
    def reservar_ip(self, nombre, etiquetas):
        a = self._r("GET", f"regions/{self.region}/addresses/{nombre}")
        if a is None:
            self._r("POST", f"regions/{self.region}/addresses", json={"name": nombre, "addressType": "EXTERNAL", "networkTier": "PREMIUM",
                                                                    "labels": etiquetas, "description": "FaceAdmin · demo instantánea"})
            for _ in range(20):
                a = self._r("GET", f"regions/{self.region}/addresses/{nombre}")
                if a and a.get("address"):
                    break
                time.sleep(1.5)
        if not a or not a.get("address"):
            raise ErrorGCP("GCP no entregó la IP reservada a tiempo")
        return a["address"]

    def liberar_ip(self, nombre):
        if not nombre.startswith(self.cfg["prefijo"]):
            raise ErrorGCP("La IP no es de una demo")
        return self._r("DELETE", f"regions/{self.region}/addresses/{nombre}")

    # --- ciclo de vida de la instancia
    def crear(self, nombre, etiquetas, metadatos, ip, vence_iso=None):
        """Crea la VM desde la imagen dorada (virtualización anidada habilitada) con etiquetas y metadatos de la demo.
        vence_iso: tope duro en GCP (terminationTime con acción DELETE) por si FaceAdmin no alcanzara a eliminarla."""
        if not nombre_valido(nombre) or not nombre.startswith(self.cfg["prefijo"]):
            raise ErrorGCP("Nombre de instancia inválido")
        etiquetas = dict(etiquetas, **{ETIQUETA: "si"})
        cuerpo = {
            "name": nombre, "machineType": f"zones/{self.z}/machineTypes/{self.cfg['tipo']}",
            "labels": etiquetas, "description": "FaceAdmin · demo instantánea (se elimina sola al vencer)",
            "advancedMachineFeatures": {"enableNestedVirtualization": True},
            "minCpuPlatform": "Intel Cascade Lake" if self.cfg["tipo"].startswith("n2-") else None,
            "disks": [{"boot": True, "autoDelete": True, "initializeParams": {
                "sourceImage": self.url_imagen() if self.url_imagen().startswith("http") else f"projects/{self.p}/{self.url_imagen()}",
                "diskSizeGb": str(int(self.cfg["disco_gb"])), "diskType": f"zones/{self.z}/diskTypes/pd-balanced", "labels": etiquetas}}],
            "networkInterfaces": [{"network": f"global/networks/{self.cfg['red']}",
                                   "accessConfigs": [{"name": "externa", "type": "ONE_TO_ONE_NAT", "natIP": ip, "networkTier": "PREMIUM"}]}],
            "metadata": {"items": [{"key": k, "value": str(v)} for k, v in metadatos.items()]},
            "tags": {"items": ["faceadmin-demo", "http-server", "https-server"]},   # 80 solo para el certificado (ACME)
            "scheduling": {"onHostMaintenance": "MIGRATE", "automaticRestart": True},
        }
        if not cuerpo["minCpuPlatform"]:
            cuerpo.pop("minCpuPlatform")
        if vence_iso:
            cuerpo["scheduling"].update({"terminationTime": vence_iso, "instanceTerminationAction": "DELETE"})
        return self._r("POST", f"zones/{self.z}/instances", json=cuerpo)

    def encender(self, nombre):
        if self._propia(nombre) is None:
            raise ErrorGCP(f"La instancia {nombre} no existe")
        return self._r("POST", f"zones/{self.z}/instances/{nombre}/start")

    def apagar(self, nombre):
        if self._propia(nombre) is None:
            raise ErrorGCP(f"La instancia {nombre} no existe")
        return self._r("POST", f"zones/{self.z}/instances/{nombre}/stop")

    def eliminar(self, nombre):
        """Elimina la VM (sus discos tienen autoDelete) y la IP reservada. Idempotente: lo que ya no existe se da por eliminado."""
        ops = []
        if self._propia(nombre) is not None:
            ops.append(self._r("DELETE", f"zones/{self.z}/instances/{nombre}"))
        return ops

    def estado(self, nombre):
        """RUNNING, STAGING, PROVISIONING, STOPPING, TERMINATED (apagada) … o None si no existe."""
        i = self.instancia(nombre)
        return (i or {}).get("status")
