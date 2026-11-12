# Demo instantánea en GCP (AWX + EDA comunitarios, sin RHEL ni OpenShift)

Estado: **fase 2**. La demo se crea **solo desde FaceAdmin**: en la oportunidad, panel «Demo o PoC» › **Demo de FaceOps** crea la Demo y su VM
en un paso (n2-standard-4, 1 semana por defecto y 14 días como máximo, horario hábil, apagado sin uso, eliminación al vencer).
FaceAdmin usa la cuenta de servicio adjunta a su VM (`faceadmin-demos@…`, por metadatos: sin clave JSON). Imagen base: `construir-imagen.py`.

Pruebas: `prueba_demo_gcp.py` (API simulada) y `prueba_real_demo.py` (crea UNA demo real, verifica acceso, apagado programado y eliminación, y limpia).

## 1. Factibilidad

Sí es factible, con estos límites:

| Pieza del Invoke lab | En la demo GCP | Nota |
|---|---|---|
| AAP 2.7 (gateway + controller 4.8 + EDA + hub) | AWX 24.6.1 (awx-operator 2.19.1) + eda-server comunitario sobre k3s | AWX pausó sus versiones públicas en 24.6.1 (2024): API estable pero sin soporte ni parches. |
| VM RHEL | Rocky Linux 9 (imagen pública sin costo de licencia) | Se pide al instante, sin suscripción. |
| VMs anidadas RHEL | 3 VMs Rocky 9 (1 vCPU, 2 GB) en KVM/libvirt | La anidada solo corre en Intel (N2/N1/C3), no en E2. |
| OpenShift SNO, k3s/Tekton | Fuera de la demo | k3s queda solo como base de AWX/EDA. |
| Automation Hub | galaxy.ansible.com | Sin colecciones certificadas `redhat.*`; usar equivalentes comunitarios (p. ej. fedora.linux_system_roles). |
| EE / DE de registry.redhat.io | quay.io/ansible/awx-ee y de-minimal | Sin login de Red Hat. |
| FaceOps, Gitea, Vault, MinIO, Prometheus/Alertmanager/Grafana | Iguales, en podman | Sin cambios. |

### FaceOps contra AWX/EDA (comparado con AAP 2.7, que usa hoy)

Funciona igual (misma API del controller con otro prefijo): plantillas, workflows, encuestas, inventarios, hosts y facts,
lanzamientos, credenciales, salida de trabajos y **aprobaciones de workflow** (`workflow_approvals/…/approve|deny` y
`create_approval_template` existen en AWX). EDA: las rutas `/api/eda/v1/…` son las mismas; cambia la URL base (EDA vive en su propio servicio, no detrás del gateway).

Hay que adaptar en `faceops/app/aap.py` (capa de compatibilidad por configuración, `aap_modo = aap | awx`):

| Punto | AAP 2.7 | AWX | Cambio |
|---|---|---|---|
| Prefijo del controller | `/api/controller/v2/` | `/api/v2/` | `self.base` según el modo. |
| Ping / prueba de conexión (conexiones.py) | `/api/controller/v2/ping/` | `/api/v2/ping/` | Ruta según el modo. |
| Validar usuario (fuentes_usuarios.py) | `/api/gateway/v1/me/` | `/api/v2/me/` | Ruta según el modo. |
| EDA (alertas.py) | `aap.web + /api/eda/v1/` | `FACEOPS_EDA_URL + /api/eda/v1/` | URL base propia de EDA. |
| Token | token del gateway | token OAuth2 de AWX (`awx-manage create_oauth2_token`) | Solo cambia cómo se emite. |
| Licencia (licencias.py, bloqueo_admin.py, replicar_maquina.py) | `config/ → license_info` | AWX no tiene suscripción | Tratar `license_info` vacío como «comunitario, sin suscripción» y no bloquear. |
| Enlaces a la UI (`url_trabajo`, alertas.py) | `/execution/jobs/playbook/<id>/output` | `/#/jobs/playbook/<id>/output` | Plantilla de URL según el modo. |
| Colecciones certificadas de los playbooks | `redhat.*`, hub privado | Galaxy | Revisar los playbooks del catálogo que usan colecciones certificadas. |

Estimación: 1–2 días de adaptación + la regla de prueba de FaceOps sobre las automatizaciones del catálogo de la demo.

## 2. Tamaño y costo (southamerica-west1, a pedido, nov-2026)

| Componente | RAM aprox. |
|---|---|
| SO + k3s | 1,5 GB |
| AWX (web, task, Postgres, Redis) + EE en ejecución | 4–5 GB |
| EDA (api, worker, scheduler, Postgres) | 2 GB |
| FaceOps, Gitea, Vault, MinIO, Prometheus/Alertmanager/Grafana, Caddy | 3,5 GB |
| 3 VMs anidadas Rocky 9 | 6 GB |
| **Total** | **~18 GB** |

- **Elegido: n2-standard-4** (pedido de Cristian; 2 VMs anidadas de 1,5 GB y requests mínimos de AWX/EDA). Antes se recomendaba n2-standard-8 (8 vCPU = 4 cores físicos, 32 GB), disco 100 GB pd-balanced: **USD 0,5555/h** encendida.
  Mínimo: n2-standard-4 (2 cores físicos, 16 GB) a USD 0,2778/h con 2 VMs anidadas de 1,5 GB: justo, con riesgo de lentitud al correr trabajos.
- Fijos aunque esté apagada: disco ~USD 0,143 GB-mes (100 GB ≈ USD 3,3/semana) + IP reservada (~USD 0,01/h sin uso).
- **Tope por demo** (1 semana, L–V 8–19 = 55 h): ≈ USD 31 de cómputo + ≈ USD 5 fijos ≈ **USD 36**. Con uso real de ~15 h: ≈ USD 13.
- Rocky no paga licencia (RHEL en GCP suma ~USD 0,13/h en 8 vCPU). Spot (USD 0,142/h) no se recomienda: GCP puede cortar la demo con el cliente.

## 3. Cómo funciona en FaceAdmin

1. **Configuración** (Demos y PoC › Demo instantánea en GCP): proyecto, zona, imagen (`familia:faceops-demo`), tipo, disco, red, URL pública de FaceAdmin,
   duración (máx. 7 días), aviso previo, inactividad y horario por defecto. La **clave JSON de la cuenta de servicio** se sube ahí: queda cifrada
   (Fernet) en `/var/lib/faceadmin/gcp_sa.enc` con permisos 600 y la llave en otro archivo 600; nunca en la BD, el repo ni los logs. «Probar conexión» valida token, zona, tipo, imagen y listado.
2. **Pedir**: en la Demo (desde la oportunidad) → «Crear demo instantánea»: zona horaria del cliente, días y horas hábiles, inactividad, usuario del cliente y correo de aviso.
3. **Crear**: IP estática reservada → VM `demo-<número>` desde la imagen dorada, con anidada, etiquetas (`faceadmin-demo=si`, demo, cliente, expira),
   metadatos (URL de FaceAdmin, token de la VM, horario) y `terminationTime` = vencimiento con acción DELETE (red de seguridad en GCP). Acceso: `https://faceops.<IP>.sslip.io` (Caddy + Let's Encrypt).
4. **Primer arranque** (`primer-arranque.sh`): la VM pide **una sola vez** el usuario y la clave del cliente a FaceAdmin y los fija en FaceOps, AWX, Gitea y Grafana.
5. **Uso**: `agente-uso.sh` cada 5 min informa conexiones HTTPS, SSH y trabajos de AWX; FaceAdmin responde si debe apagarse. Sin uso por N minutos se apaga sola;
   si no, la apaga FaceAdmin. Fuera de horario siempre se apaga.
6. **Encender**: botón en FaceAdmin o enlace público «encender mi demo» (con token, sin cuenta) para el cliente, solo en horario hábil; opcional: arranque programado al inicio del horario.
7. **Vencer**: aviso por correo 24 h antes; al vencer se elimina la VM (discos con autoDelete), la IP reservada, los tokens y la clave cifrada; la Demo queda cerrada.
   La bóveda y las credenciales internas viven dentro de la VM y se van con ella. Con sslip.io no hay DNS que limpiar.

Estados: pedida → creando → activa ⇄ apagada → vencida → eliminada (además: eliminando, error). El ciclo lo corre un hilo de FaceAdmin cada minuto.

## 4. Cuenta de servicio (permisos mínimos)

```bash
P=master-ia-320218; SA=faceadmin-demos@$P.iam.gserviceaccount.com
gcloud iam service-accounts create faceadmin-demos --project=$P --display-name="FaceAdmin · demos instantáneas"
# Lectura (sin condición): zonas, tipos, imágenes, red, operaciones y listado
gcloud iam roles create faceadminDemosLectura --project=$P --title="FaceAdmin demos · lectura" --permissions=\
compute.instances.list,compute.zones.get,compute.machineTypes.get,compute.images.get,compute.images.getFromFamily,compute.images.useReadOnly,\
compute.networks.get,compute.subnetworks.use,compute.subnetworks.useExternalIp,compute.zoneOperations.get,compute.regionOperations.get
# Cambios (con condición: solo recursos demo-*)
gcloud iam roles create faceadminDemosCambios --project=$P --title="FaceAdmin demos · cambios" --permissions=\
compute.instances.create,compute.instances.get,compute.instances.start,compute.instances.stop,compute.instances.delete,\
compute.instances.setMetadata,compute.instances.setLabels,compute.instances.setTags,compute.disks.create,compute.disks.setLabels,\
compute.addresses.create,compute.addresses.get,compute.addresses.delete,compute.addresses.use,compute.addresses.setLabels
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=projects/$P/roles/faceadminDemosLectura --condition=None
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=projects/$P/roles/faceadminDemosCambios \
  --condition='title=solo-demos,expression=resource.name.matches("/(instances|disks|addresses)/demo-")'
# (validar la condición con una prueba real al crear la cuenta; FaceAdmin además exige prefijo demo- y etiqueta faceadmin-demo=si)
gcloud iam service-accounts keys create /tmp/fa-demos.json --iam-account=$SA
# Subir /tmp/fa-demos.json en FaceAdmin y borrarlo del equipo: shred -u /tmp/fa-demos.json
```

Reglas de firewall: tráfico 80/443 a las VMs con etiqueta `http-server`/`https-server` (80 solo para el certificado). La VM debe poder llegar a FaceAdmin por HTTPS (8843).

## 5. Imagen base (familia `faceops-demo`)

SO: **Rocky Linux 9** (imagen pública de GCP sin costo de licencia, binariamente compatible con RHEL: los playbooks de FaceOps corren igual).

Se construye desde la VM de FaceAdmin, con su cuenta de servicio: `python3 herramientas/demo-gcp/construir-imagen.py` → VM constructora
`demo-constructor-<fecha>` (n2-standard-4, anidada, tope de 4 h con DELETE) cuyo startup-script clona la rama y corre `preparar-vm.sh`:
k3s (nodo fijo en una interfaz ficticia, independiente de la IP de GCP) + AWX 24.6.1 + EDA comunitarios, FaceOps en modo awx con validador local,
2 VMs Rocky anidadas (nodo-web, nodo-app) más la propia VM (nodo-prueba) en el inventario de AWX, Caddy, primer arranque y agente de uso.
**Antes de generalizar corre la prueba real `prueba-awx/e2e.py`: si falla, no hay imagen.** Se guardan las 2 últimas imágenes de la familia.
El primer arranque de cada demo renueva todas las claves internas (admin de AWX/EDA, token de FaceOps, credencial EDA→AWX) y crea el usuario del cliente. Tiempo de creación de una demo desde la imagen: ~3–5 min (VM) + ~3–5 min (arranque de AWX/EDA). Medido: ver `prueba_real_demo.py`.

## 6. Decisiones pendientes

1. ¿Se acepta AWX/EDA comunitario (sin soporte, sin parches desde 24.6.1, sin colecciones certificadas) para demos de clientes?
2. Tamaño: n2-standard-8 (recomendado, ~USD 36 tope por demo) o n2-standard-4 (más barato, más lento).
3. Distro: Rocky Linux 9 (recomendado) / Alma 9 / CentOS Stream 9.
4. Cuenta de servicio: crearla con el rol y la condición de arriba y cargar su clave en FaceAdmin.
5. Implementar la capa `aap_modo=awx` en FaceOps (sección 1) antes de construir la imagen.
