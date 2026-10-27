# Demo 4 · Redes

Laboratorio de red emulado con containerlab dentro de la VM, gestionado por la misma AAP.

**Fase 1 (sin imágenes con licencia):** ISP simulado y router de borde con FRR (BGP entre ellos), `srv-app` (HTTP :8080) y `pc-usuario`. Caso 2, configuración como código con detección de drift, funcional sobre el router.

**Caso 1 (firewall, verificado):** firewall **VyOS** (código abierto, colección `vyos.vyos`) entre el router y la red de usuarios: WAN 10.20.20.2 hacia `rtr`, LAN 10.40.40.1 hacia `pc-usuario`, gestión 172.20.20.12. Por defecto pc-usuario sale a Internet pero no llega a `srv-app:8080`; la plantilla "Red · Solicitar acceso" crea la regla, la prueba y la registra en ServiceNow. Reemplaza al FortiGate, cuya licencia de evaluación no se pudo activar (ver al final).

**Fase 2:** Arista cEOS (núcleo y dos switches), preparado en la topología.

| Ruta | Para qué |
|---|---|
| `instalar-fw.sh` | Imagen de VyOS (desde la ISO rolling), clave del firewall en Vault (`secret/red/firewall`), despliegue del laboratorio y `configurar_fw_aap.py` (host `fw`, credencial desde Vault, plantilla y archivos en Gitea). |
| `instalar-lab.sh` | Instala containerlab, construye la imagen FRR con SSH, despliega el laboratorio y configura Gitea, Vault y AAP. |
| `configurar_red_aap.py` | Repositorio `invoke/red-laboratorio` en Gitea, `secret/red/ssh` en Vault y, en AAP, la organización "Redes" con su inventario, credencial, proyecto y plantillas. |
| `lab/` | Topología `summit-red.clab.yml` y configuraciones base. |
| `red-laboratorio/` | Contenido inicial del repositorio de red: `config/rtr.conf` es el estado deseado y `playbooks/` lo que ejecuta AAP. |
| `herramientas/` | `probar-red.sh`, `romper-red.sh`, `consola-rtr.sh`, `consola-fw.sh`, `probar-caso1.py`, `probar-solicitud.py`, `reporte-solicitudes.py`, `vaciar-accesos.py`, `cerrar-app-bd.sh`, `reiniciar-lab.sh`. |
| `servicenow/configurar_revocacion.py` | Formulario con listas, rangos y "Vigente hasta"; ítem "Revocar acceso de red"; plantillas y workflow de revocación y la programación diaria de vencidos. |

Direcciones de gestión: isp .10 · rtr .11 · fw .12 · core .13 · swa .14 · swb .15 · srv-app .21 · pc-usuario .22 (172.20.20.0/24).

## Estado (fase 1, verificada)

- Laboratorio corriendo: `isp` (.10) y `rtr` (.11) con FRR 10.2, `srv-app` (.21, python:3.12-alpine con HTTP :8080) y `pc-usuario` (.22, alpine). Consumo real: menos de 100 MB de RAM.
- AAP, organización "Redes": inventario y credencial "Red · laboratorio" (la llave sale de Vault `secret/red/ssh`), plantillas "Red · Aplicar configuración", "Red · Verificar drift" (además programada cada 10 minutos como respaldo) y "Red · Solicitar acceso" (firewall VyOS; credenciales "Red · firewall VyOS" desde Vault y "ServiceNow · ansible.automatizacion").
- EDA: activación "Red · Drift por eventos" (rulebook `rulebooks/drift-red.yml`, webhook :5000, entorno de decisión del Hub local). El vigía del router (`lab/config/vigia.py`) avisa cuando la configuración cambia fuera de Git.

## Cómo hacer la demo (caso 1: pedir un acceso por ticket)

1. Antes de empezar: `bash ~/summit-redhat/demo4-redes/herramientas/reiniciar-lab.sh` y `python3 ~/summit-redhat/demo4-redes/herramientas/vaciar-accesos.py` → `pc-usuario → srv-app:8080 : BLOQUEADO` y el registro de accesos vacío.
2. En ServiceNow, catálogo **"Solicitar acceso de red"** (categoría Red):
   - **Origen**: una o más redes/IPs separadas por coma, o el grupo `servidores-app` (los dos JBoss) o `usuarios`.
   - **Destino**: la IP del servidor.
   - **Puertos**: uno (`8080`), varios (`8080, 8443`) o un rango (`8000-8010`), hasta 10.
   - **Vigente hasta** (opcional): el último día que debe funcionar; al día siguiente se retira solo.
3. EDA lo recibe y el workflow **"Red · Solicitar acceso con aprobación"** prepara los comandos (sin aplicar) y espera la aprobación (correo + Workflow Approvals o "Red · Responder solicitud").
4. Al aprobar: firewall de red (VyOS) → un *port-group* con todos los puertos y **una** regla; servidor con firewalld (base de datos) → una regla por origen y puerto. Prueba cada puerto y cierra el ticket: "Listo. Habilitamos el acceso … por los puertos 8080, 8443. Lo probamos y funciona." El acceso queda en el registro `accesos/registro.yml` de Gitea.
5. Comprobar: `bash ~/summit-redhat/demo4-redes/herramientas/probar-red.sh`. En el firewall: `bash ~/summit-redhat/demo4-redes/herramientas/consola-fw.sh` → `show firewall`.

Prueba de punta a punta desde la VM (pide el ticket como usuario, aprueba y muestra lo que ve el usuario):
`python3 ~/summit-redhat/demo4-redes/herramientas/probar-solicitud.py acceso origen=10.40.40.0/24 destino=10.10.10.10 "puerto=8080, 8443"` (opciones `--decision=Rechazar --motivo=…`, `--decision=ninguna`, `--limpiar`; `probar-solicitud.py limpiar RITM…` borra tickets de prueba).

### Manejo de excepciones (todo en el playbook)
| Caso | Qué pasa | Estado final del ticket |
|---|---|---|
| Dato inválido (puerto 70000, IP mal escrita, fecha pasada) | Precheck: no se toca nada | **Rechazado**, con el dato a corregir |
| Nadie aprueba en 4 h | No se aplica nada | **Timeout** ("no alcanzó a ser revisada…") |
| El aprobador rechaza | No se aplica nada | **Rechazado**, con el motivo del aprobador |
| Puerto sin servicio o IP inexistente (`puerto=9999`) | Se crea la regla, la prueba falla (1 reintento), **la regla se borra sola** | **Fallido y escalado**: abierto, asignado a "Automatización Ansible", con el diagnóstico y lo que se deshizo |
| Todo bien | Regla aplicada y probada | **Completado**, con la evidencia en notas de trabajo |

Reporte: `python3 ~/summit-redhat/demo4-redes/herramientas/reporte-solicitudes.py [días]` (cuántas por estado y las fallidas abiertas).

### Retirar un acceso y vencimiento
- Catálogo **"Revocar acceso de red"**: número del ticket original (o servidor de destino y, si se quiere, origen y puerto) → workflow **"Red · Revocar acceso con aprobación"** → quita la regla, comprueba que ya no llega, lo saca del registro, avisa en el ticket original y cierra: "Listo. Retiramos el acceso…".
- **"Red · Revocar accesos vencidos"** corre todos los días a las 00:15 (programación en AAP): retira los accesos cuyo "Vigente hasta" ya pasó y deja la nota en el ticket original. Para probar: lanzarla con la variable `hoy: AAAA-MM-DD` (una fecha futura).

### Caso app → base de datos (dos JBoss, un ticket)
1. `bash ~/summit-redhat/demo4-redes/herramientas/cerrar-app-bd.sh` → las dos apps (`http://192.168.122.15:8080/app-jboss/` y `.18`) muestran "Base de datos: sin conexión".
2. Ticket "Solicitar acceso de red": origen `servidores-app`, destino `192.168.122.13`, puerto `5432` → una regla por JBoss en el firewall del servidor de base de datos; la prueba es que ambas apps muestran "conectada".
3. Para cerrarlo de nuevo: ticket "Revocar acceso de red" con ese número (corta también las conexiones abiertas) o `cerrar-app-bd.sh`.

## Cómo hacer la demo (caso 2)

1. Antes de empezar: `bash ~/summit-redhat/demo4-redes/herramientas/reiniciar-lab.sh` (unos 20 s, deja todo en OK).
2. Mostrar en Gitea `invoke/red-laboratorio` → `config/rtr.conf`: es la única forma válida de cambiar la red.
3. Cambio a mano, como lo haría alguien por consola: `bash ~/summit-redhat/demo4-redes/herramientas/romper-red.sh` (borra el vecino BGP: pc-usuario pierde Internet). Para hacerlo "en vivo" dentro del router: `bash ~/summit-redhat/demo4-redes/herramientas/consola-rtr.sh` y luego `conf t` → `router bgp 65010` → `no neighbor 10.0.0.1`.
4. En menos de un minuto: en AAP aparece el job "Red · Verificar drift" lanzado por Event-Driven Ansible, con el cambio que reportó el equipo, el DRIFT DETECTADO y la restauración.
5. Comprobar: `bash ~/summit-redhat/demo4-redes/herramientas/probar-red.sh` → todo OK.
6. Cambio por el camino correcto: editar `config/rtr.conf` en Gitea (por ejemplo, agregar `network 10.30.30.0/24`) y lanzar "Red · Aplicar configuración".

Diagnóstico: `python3 ~/summit-redhat/demo4-redes/herramientas/eda-estado.py` (estado y registro de la activación).
Si se cambió el estado deseado durante una demo y se quiere volver a la base: `python3 ~/summit-redhat/demo4-redes/configurar_eda_red.py --config`.

## Fase 2: imágenes que se descargan con cuenta propia

**Arista cEOS** (cuenta gratuita en arista.com):
1. arista.com → Support → Software Download → EOS → cEOS-lab → la versión 4.32.x → archivo `cEOS64-lab-4.32.2F.tar.xz` (unos 500 MB).
2. Subirlo a la VM: en "SSH en el navegador" de la consola de GCP, botón **Subir archivo** (queda en `~`).
3. En la VM: `sudo podman import ~/cEOS64-lab-4.32.2F.tar.xz ceos:4.32.2F`

**FortiGate VM de evaluación** (no se usa: la licencia de evaluación no se pudo activar con dos cuentas FortiCare). El nodo `fgt` quedó comentado en `lab/summit-red.clab.yml` con la nota para reactivarlo, su disco se conserva en `/opt/summit-red/fgt-disco` y el host `fgt` está deshabilitado en AAP. El flujo es el mismo con la colección certificada `fortinet.fortios`; para volver a él:
1. support.fortinet.com → Support → VM Images → producto FortiGate, plataforma KVM → `FGT_VM64_KVM-v7.4.x.F-buildXXXX-FORTINET.out.kvm.zip`.
2. Subirlo a la VM igual que el anterior.
3. La imagen se convierte en contenedor con vrnetlab (eso lo hace Claude). La licencia de evaluación permanente se activa con la cuenta FortiCare desde el propio FortiGate y tiene límites (interfaces, políticas, rutas).


## Caso: IP sospechosa (seguridad, sin aprobación)

1. En la VM: `bash ~/summit-redhat/demo4-redes/herramientas/simular-alerta-siem.sh` (simula la alerta de un SIEM: 45 intentos desde 203.0.113.50).
2. EDA recibe la alerta y AAP ("Red · Bloquear IP") agrega la IP al grupo `bloqueadas` del firewall VyOS: el script muestra LLEGA → BLOQUEADA.
3. Se abre un incidente en ServiceNow `[Demo 4 · Seguridad] IP … bloqueada automáticamente`, asignado a "Automatización Ansible", y llega un correo a cquezada@invoke.cl.
4. Para cerrar el caso, resolver el incidente en ServiceNow con **Código de resolución**:
   - **Falso positivo** → Ansible levanta el bloqueo y deja la nota.
   - **Ataque confirmado** → la IP sigue bloqueada.
5. Repetir la demo: `bash ~/summit-redhat/demo4-redes/herramientas/reiniciar-seguridad.sh`.

## Caso: preparar red para clúster OpenShift

1. En ServiceNow, catálogo **"Preparar red para clúster OpenShift"** (categoría Red): nombre del clúster (ej. `ocp-demo`), nodos y motivo → **Order Now**.
2. EDA lo recibe y el workflow **"Red · Preparar red OpenShift con aprobación"** muestra los cambios (paso *preparar*) y espera la aprobación (Workflow Approvals o "Red · Responder solicitud"; llega el correo de aprobación).
3. Al aprobar, AAP: asigna un /24 libre de `10.60.0.0/16` y lo registra en Gitea (`ipam/clusters.yml`); declara la ruta y el anuncio BGP en `config/rtr.conf` (commit) y lo aplica al router; crea la interfaz y la regla 2NN en el firewall (6443, 22623, 80, 443); crea `api`, `api-int` (VIP .5) y `*.apps` (VIP .6) en el DNS del laboratorio (dnsmasq, 172.20.20.30).
4. Prueba (el ISP recibe la ruta, el DNS resuelve, la regla existe) y cierra el ticket: "Listo. Preparamos la red del clúster…".

- DNS del laboratorio: `bash herramientas/instalar-dns.sh` (una vez). Probar: `sudo podman exec summit-dns nslookup api.ocp-demo.lab 127.0.0.1`.
- Repetir la demo: `bash herramientas/reiniciar-openshift.sh` (Git, router, firewall y DNS vuelven a la base; no toca accesos ni IPs bloqueadas).
