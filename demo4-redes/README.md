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
| `herramientas/` | `probar-red.sh`, `romper-red.sh`, `consola-rtr.sh`, `consola-fw.sh`, `probar-caso1.py`, `reiniciar-lab.sh`. |

Direcciones de gestión: isp .10 · rtr .11 · fw .12 · core .13 · swa .14 · swb .15 · srv-app .21 · pc-usuario .22 (172.20.20.0/24).

## Estado (fase 1, verificada)

- Laboratorio corriendo: `isp` (.10) y `rtr` (.11) con FRR 10.2, `srv-app` (.21, python:3.12-alpine con HTTP :8080) y `pc-usuario` (.22, alpine). Consumo real: menos de 100 MB de RAM.
- AAP, organización "Redes": inventario y credencial "Red · laboratorio" (la llave sale de Vault `secret/red/ssh`), plantillas "Red · Aplicar configuración", "Red · Verificar drift" (además programada cada 10 minutos como respaldo) y "Red · Solicitar acceso" (firewall VyOS; credenciales "Red · firewall VyOS" desde Vault y "ServiceNow · ansible.automatizacion").
- EDA: activación "Red · Drift por eventos" (rulebook `rulebooks/drift-red.yml`, webhook :5000, entorno de decisión del Hub local). El vigía del router (`lab/config/vigia.py`) avisa cuando la configuración cambia fuera de Git.

## Cómo hacer la demo (caso 1)

1. Antes de empezar: `bash ~/summit-redhat/demo4-redes/herramientas/reiniciar-lab.sh` → `pc-usuario → srv-app:8080 : BLOQUEADO` (borra las reglas pedidas).
2. En AAP, lanzar "Red · Solicitar acceso" (encuesta: origen 10.40.40.0/24, destino 10.10.10.10/32, puerto 8080, solicitado por). Abre un Change en ServiceNow con la tarea "Firewall: permitir 10.40.40.0/24 → 10.10.10.10:8080", crea en VyOS los grupos y la regla (con quién la pidió y el número de job), la prueba desde pc-usuario y cierra el Change con la evidencia. Desde la VM: `python3 ~/summit-redhat/demo4-redes/herramientas/probar-caso1.py`.
3. Comprobar: `bash ~/summit-redhat/demo4-redes/herramientas/probar-red.sh` → `srv-app:8080 : OK`. En el firewall: `bash ~/summit-redhat/demo4-redes/herramientas/consola-fw.sh` → `show firewall`.

Si se lanza otra vez con los mismos datos, no cambia nada ni abre ticket.

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
