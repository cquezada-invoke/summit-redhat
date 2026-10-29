# Pendientes de las demos del Summit

## FaceOps · segmentos de negocio desde su arquitectura (hecho la noche del 28-10-2026, para revisar)
- Solicitud de segmento = ticket «Solicitar nuevo segmento de negocio» con el manifiesto YAML adjunto
  (Segmentos de negocio › «+ Solicitar nuevo segmento de negocio»). Workflow en AAP «FaceOps · Nuevo segmento de negocio»:
  aprobación de Arquitectura → aprobación de Infraestructura → cierre. Infraestructura decide por componente y
  ambiente: parque nuevo o máquinas existentes compatibles (primero las que ya tienen el producto).
- Al aprobarse: el manifiesto final (con la decisión de Infraestructura) queda en Gitea `invoke/arquitectura` y el
  segmento se activa con su plan (crear servidores, habilitar productos, abrir flujos) y su costo.
- Costo de infraestructura por segmento (mensual, anual e inversión incremental) con tarifas referenciales en
  `faceops/datos/costos.yml`: **validar las tarifas** (vCPU, GB RAM, GB disco, licencias, operación, appliances de red).
- Servicios de red en la arquitectura: firewall, WAF, balanceador, proxy, router; los flujos declaran `via` y el mapa
  dibuja el camino (p. ej. Clientes → FortiGate FIREWALL + WAF → Nginx).
- Usuarios nuevos del laboratorio: proyecto.lider (solicita), arq.lider (rol Arquitectura), infra.lider (rol
  Infraestructura). Claves en /var/lib/faceops/credenciales-iniciales.txt. Los roles se asignan en FaceOps-admin › usuario.
- Prueba hecha: FO-LAB-00019 «Ventas en línea» (aprobado de punta a punta como prueba, con app y base en máquinas
  existentes; QA queda pendiente de crear). En Gitea quedó además un archivo huérfano `ventas-en-lnea.yml` (nombre
  sin tilde de la primera versión): borrarlo cuando se confirme.
- Pendiente: las aprobaciones en AAP salen con el usuario de servicio de FaceOps (no con la persona); el YAML final
  pierde los comentarios del original (se guarda el original en el ticket como adjunto).

## Manejo de excepciones (decidido por Cristian el 26-10-2026) · ✅ HECHO en la Demo 4 (27-10-2026; falta Demo 3 y 5)
Toda la validación vive en el **playbook**, no en el formulario de ServiceNow. Que la respuesta llegue
unos minutos después en el ticket está bien: hoy ningún ticket es instantáneo.

Reglas para las demos 3, 4 y 5:
1. **Precheck en el playbook antes de actuar:** formato de los datos, que el destino exista y responda,
   permisos y capacidad. Si algo falla, no se toca nada y se responde en el ticket.
2. **Todo o nada:** si algo falla a mitad de camino, Ansible revierte lo que hizo (regla, cola, rol,
   tabla). No quedan objetos huérfanos.
3. **Ningún ticket en el limbo.** Siempre termina en un estado final:
   - Completado, con evidencia.
   - Rechazado, con el motivo para el usuario.
   - Fallido y escalado, asignado a una persona con diagnóstico y con lo que se revirtió.
   - Timeout: si no termina en N minutos, se corta, se revierte y se escala.
4. **Reintentos acotados** (máximo uno), nunca en bucle.
5. **Visibilidad:** un reporte en ServiceNow de automatizaciones fallidas o escaladas y un aviso por correo.

Caso para la demo en vivo: se pide un puerto o una IP inexistente, la regla se crea, la prueba falla,
la regla se borra sola y el ticket queda "Fallido y escalado" o "Rechazado" con la explicación.
- Demo 4 (probado de punta a punta): precheck, todo o nada con rollback, Completado / Rechazado / Fallido y escalado /
  Timeout, un reintento, reporte `herramientas/reporte-solicitudes.py`. Falta el aviso por correo de los fallidos
  (hoy quedan asignados al grupo "Automatización Ansible") y llevar el modelo a las demos 3 y 5.

## Lenguaje del ticket para el usuario (pedido por Cristian el 26-10-2026)
El usuario final no sabe qué hay detrás: nada de "Ansible", "EDA", "AAP" ni "firewall" en lo que ve.
- Descripción del ítem "Solicitar acceso de red": texto simple ("Indica desde dónde, hacia qué servidor y
  qué puerto necesitas. Te avisaremos aquí cuando quede habilitado").
- Respuesta de cierre del RITM: declarar exactamente el cambio hecho, en lenguaje humano. Ejemplo:
  "Listo. Habilitamos el acceso desde la red 10.10.0.0/24 al servidor 172.30.0.10 por el puerto 443.
  Lo probamos y funciona." La evidencia técnica va en notas de trabajo (work notes), que no ve el usuario.
- Lo mismo para los rechazos y fallas del modelo de excepciones.
- Al cerrar, llenar el campo **Duration** (calendar_duration) del RITM con el tiempo real entre creación y
  cierre, para verlo como columna en la lista; y decirlo en la respuesta ("resuelto en 1 min 12 s").
- Archivos: `servicenow/configurar_catalogo.py` y `playbooks/tareas/snow_ritm.yml` (demo4-redes).

## ✅ HECHO (27-10-2026) · Demo 4: más de un puerto por ticket (pedido por Cristian el 26-10-2026)
- Aceptar en el campo Puerto una lista o rango ("8080, 8443" o "8000-8010").
- VyOS: un port-group con todos los puertos y una sola regla. firewalld: una rich rule por puerto.
- Aprobación con todos los comandos juntos; prueba de cada puerto; el ticket se cierra solo si todos responden.
- También varios orígenes (lista o grupo `servidores-app`), "Vigente hasta" con retiro diario automático y el ítem
  "Revocar acceso de red" con aprobación (README de demo4-redes).
- Pendiente menor: `reiniciar-lab.sh` lo está editando otro proceso; al reiniciar el laboratorio correr también
  `herramientas/vaciar-accesos.py` (o sumarlo a reiniciar-lab.sh) para que el registro de accesos quede igual al firewall.
- Pendiente menor: el caso Timeout se probó acortando la aprobación a 60 s; la prueba sobre un servidor con firewalld
  solo existe para la base de datos (nodo-postgres); otros servidores quedan "aplicado sin prueba automática".

## ✅ HECHO (26-10-2026) · Demo 4: preparar red para clúster OpenShift
- Ítem de catálogo "Preparar red para clúster OpenShift" (nombre del clúster, cantidad de nodos) → aprobación en AAP.
- Ansible: asigna segmento desde un rango reservado; router: interfaz/VLAN y anuncio BGP (vía commit en Gitea);
  firewall: 6443, 22623, 80/443; DNS del laboratorio (agregar): api, api-int y *.apps.
- Prueba y cierre del ticket con los datos para el instalador.

## Mejora (no necesaria para la demo): instructivo de plataforma.yml
- PLATAFORMA.md en app-jboss (qué se puede pedir, ejemplos, qué crea y qué no hace Ansible) y JSON Schema validado en el pipeline.

## Eliminar la Demo 2: en pausa por decisión de Cristian (26-10-2026)

## ✅ HECHO (27-10-2026) · Demo 4: actualización masiva de "firmware" en parque mixto
- Workflow "Plataforma · Actualizar firmware": un cambio en ServiceNow con una tarea por equipo, aprobación en AAP (correo),
  canario de cada tipo y luego el resto; pre/post-chequeo; si falla, vuelve a la versión anterior y se detiene.
- Routers de sucursal suc1-3 (FRR 9.1 → 10.2.1, BGP con rtr) y parches de seguridad de nodo-so y nodo-nginx (con reinicio).
  Caso de falla: imagen "10.2.1 defectuosa". Guía en demo4-redes/README.md.
- **Siguiente paso:** switches Arista cEOS (falta la segunda versión de cEOS: la descarga Cristian) y firewall VyOS
  (se dejó fuera porque se usa para las pruebas de accesos).

## JBoss como código, varios nodos — HECHO (26-10-2026)
- nodo-jboss2 (.18), Nginx balancea; repo jboss-config (comun + nodos), drift cada 5 min, manifiesto en repo ambientes,
  "JBoss · Restablecer a versión". Guía: demo-jboss/README.md.
- ✅ HECHO (27-10-2026): nodo-jboss2 en el panel y el vigía de la Demo 1 (foto inicial, nivel corregir, base de paquetes).
  Los archivos de JBoss los sigue controlando jboss-config.
- ✅ Caso integrado app → base (Demo 4): un solo ticket con origen `servidores-app` (o las dos IPs) abre el acceso desde
  ambos JBoss; `cerrar-app-bd.sh` cierra los dos (27-10-2026).

## ✅ HECHO (27-10-2026) · Demo 1 en Windows
- nodo-windows en el panel: servicios W3SVC y W32Time (estado e inicio) y miembros de Administradores contra la base de Git
  (estado-servidores, master:nodo-windows/windows.yml). Vigía como tarea programada → panel → AAP "Drift · Windows".
- Niveles corregir (antes/después en el incidente), registrar (commit en main) y alertar. Prueba: demo-drift/herramientas/probar-windows.sh.

## Otros
- CMDB en ServiceNow alimentada por Ansible (registra los servidores reales); es opcional para la demo.
- Catálogo: categoría "Infraestructura" con "Servidores virtuales" ("Solicitar servidor RHEL", con tamaño,
  costo y aprobación para el tamaño L) y "Red". Ocultar las categorías de ejemplo.
- ✅ HECHO (26-10-2026): ServiceNow sin registros de ejemplo ni de prueba.
- Planilla: revisión completa hoja por hoja (restaurar lo perdido por la sincronización, URL por IP,
  agregar ServiceNow, VyOS, ticket a EDA a AAP, Demo 5 y la guía de ServiceNow).
- Enlaces con la IP interna 10.194.0.2 (revisado 26-10-2026): la URL base de AAP ya es https://34.176.42.56 y las
  notificaciones, tickets y correos usan esa dirección. La IP interna solo aparece en las credenciales de EDA hacia el
  controller (https://10.194.0.2/api/controller/), que deben seguir internas; por eso los enlaces "job" del Rule Audit
  de EDA apuntan a 10.194.0.2. Opción si molesta: usar un nombre (p. ej. 34-176-42-56.sslip.io) que en la VM resuelva
  a 10.194.0.2 (/etc/hosts) y afuera a la IP pública; probar antes con una activación.
- FortiGate: pedir licencia NFR a Fortinet; el disco y la topología quedan listos para reactivarlo.
- Mirror de Gitea `invoke/automatizacion` hacia GitHub (falta un token de GitHub).

## Demo 6 · conectar GCP (lo hace Cristian; 26-10-2026)
Todo lo demás está construido y probado sin la nube: el ticket "Solicitar VM en la nube" se cierra solo con
"la nube todavía no está conectada" en menos de un minuto. Proyecto: **invoke-summit-cloud** (no master-ia-320218).
1. **Compute Engine API:** consola → proyecto invoke-summit-cloud → APIs y servicios → Biblioteca → "Compute Engine API"
   → Habilitar (https://console.cloud.google.com/apis/library/compute.googleapis.com?project=invoke-summit-cloud).
2. **Cuenta de servicio** (IAM y administración → Cuentas de servicio → Crear): nombre `ansible-summit`, roles
   **Compute Instance Admin (v1)** y **Compute Security Admin** (reglas de firewall); si al crear VMs aparece un error
   de "serviceAccountUser", sumar **Service Account User**. Nada de Owner ni Editor.
   Luego: la cuenta → Claves → Agregar clave → JSON (se descarga un archivo).
3. **Llave en Vault** (en la VM, sin mostrarla en pantalla; subir el JSON con "Subir archivo" del SSH del navegador):
   `python3 ~/summit-redhat/demo6-nube/herramientas/guardar-llave-gcp.py ~/ARCHIVO.json && rm ~/ARCHIVO.json`
   y después `python3 ~/summit-redhat/demo6-nube/configurar_nube.py` (crea la credencial "GCP · invoke-summit-cloud",
   la fuente del inventario "Nube GCP" y activa las programaciones). Alternativa: pegar el JSON en AAP, credencial tipo
   "Google Compute Engine" en la organización Nube, y asociarla a las plantillas "Nube · …".
4. **Presupuesto con alerta:** Facturación → Presupuestos y alertas → Crear → proyecto invoke-summit-cloud, monto
   USD 20/mes, alertas al 50 %, 90 % y 100 % por correo.
5. **Correo:** AAP → Notificadores → "Correo · nube" → pegar la clave de aplicación de Gmail (la misma de
   "Correo · aprobaciones de red").
6. Probar: `python3 ~/summit-redhat/demo6-nube/herramientas/probar-nube.py vm nombre=prueba-web tamano=S --limpiar`
   (pide, aprueba, crea la VM, cierra el ticket) y luego `probar-nube.py baja nombre=prueba-web --limpiar`.
   Si `google.cloud` reclama una librería de Python (google-auth) que no trae el entorno de ejecución, avisarle a
   Claude: hay que construir un EE con google.cloud.

## Demo doble factor (Keycloak aparte, 27/10)
- App: http://34.176.42.56:8090/mi-app/ · teléfono/bandeja simulados: /mi-app/telefono · consola: /doble-factor/admin/
- Instalación/reinstalación: `bash ~/summit-redhat/demo-doble-factor/instalar.sh` (idempotente, no toca el Keycloak de las demos).
- Correo real: falta pegar la contraseña de aplicación de Gmail en la consola (realm invoke-2fa → Realm settings → Email). Mientras tanto el código llega a la bandeja simulada.
- SMS real: reemplazar el envío simulado por un proveedor (Twilio, AWS SNS u operador local).

## Data center 1 e IGA (28/10)
Listo y probado:
- VM agrandada a n2-standard-32 (32 vCPU, 128 GB).
- Inventario único `datacenter/inventario/dc1.yml` → `dc.py` (redes por zona, equipos, inventario AAP, publicación, secretos).
- Active Directory corp.invoke.lab (DNS, CA/LDAPS, OUs, grupos, svc-midpoint, svc-aap).
- AAP con login LDAP contra AD (Empleados entran, TI = administrador). Probado con un usuario temporal.
- CMDB de ServiceNow con los 5 equipos (`datacenter/cmdb.py`).
- Ecosistema: pestaña 7 · Data center e IGA.
Escrito, falta ejecutar (espera registro en Red Hat):
- IdM, Directory Server, midPoint, Keycloak e IGA (`sitio-linux.yml`). El servicio `dc-construir@dc1` lo corre solo apenas
  los equipos estén registrados (plantilla AAP "DC · Registrar en Red Hat", con la clave de activación de Cristian).
Esperan un insumo de Cristian:
- OpenShift SNO + MetalLB L2: pull secret en `~/datacenter/pull-secret.json` → `correr.sh playbooks/openshift-sno.yml`.
  Siguiente: escenario sin Internet con registro espejo (oc-mirror) y caso IP duplicada por ARP.
- Satellite: suscripción + manifiesto en `~/datacenter/manifiesto-satellite.zip` → plantilla "DC · Satellite".
Después: DC2 como clon (otra red_base) y alta disponibilidad.

## FaceOps v1 (28/10)
Listo y probado: consola en http://34.176.42.56:8090/faceops/ (contenedor en la VM, `faceops/desplegar.sh`).
- Catálogo desde AAP (59 automatizaciones) con ficha común, formularios, ejecución por segmento, tickets FO-LAB-#####,
  aprobación, auditoría; inventario inteligente con recolector diario en AAP (caché de facts), segmentos, skills,
  políticas y hallazgos; buscador por intención; mapa por niveles; FaceOps-admin (clientes, perfiles, tarifas, ingresos).
Mejoras del 28/10 (noche), pedidas por Cristian:
- Catálogo ordenado por las demos originales (1 a 6); el resto en «Otras automatizaciones» (datos/demos.yml).
- «Cómo funciona» por automatización: guía simple (datos/guias.yml, editable en admin) + recorrido + detalle técnico desde AAP.
- La aprobación vive en AAP (FaceOps la muestra y la responde; para plantillas sin aprobación crea «FaceOps · Aprobar › …»).
- ServiceNow orquestado desde FaceOps (datos/servicenow.yml): RITM del catálogo de la demo (red y nube), incidente propio
  de la automatización (Demo 5) o incidente de FaceOps. Los vigías de las demos esperan 25 s para no duplicar.
- Formularios amigables (datos/formularios.yml): equipos, fechas, IP, opciones; vista previa «Lo que recibirá AAP».
- Corregido: VyOS no aceptaba «configure» tras reiniciar la VM (permisos de config/tmp; fw-arranque.sh lo deja bien).
Mejoras del 28/10 (noche, 2ª tanda): segmentos de negocio (un solo sistema por segmento; infraestructura y ambiente son
columnas), un ambiente por ejecución (producción y QA con aprobación al redesplegar), JBoss nuevos (nodo-jboss3 Portal de
clientes, nodo-jboss-qa, nodo-jboss-dev), skills = automatizaciones que habilitan capacidades (solo en equipos compatibles
y sin la skill), categoría y tipo (virtual/físico) de cada equipo, equipo vacío → apagar con aprobación, «Habilitar
servidor» (RHEL 9 / Ubuntu 24.04 / Windows 2022, on-premise o GCP, réplicas y recursos), consola SSH y RDP en el navegador
(Guacamole), acciones directas sobre un equipo, buscadores en Automatizaciones e Inventario, gráficos de valor y efectividad,
duración total / ejecución / aprobación.
Siguientes:
- Probar «Habilitar servidor» de punta a punta (on-premise Ubuntu/RHEL; Windows ≈25 min; GCP necesita la credencial de GCP).
- RDP en dc1-ad01 (hoy cerrado) y recolección de equipos de red (versión, interfaces).
- Que la aprobación quede en AAP a nombre de la persona (hoy la responde el usuario de servicio «faceops»; FaceOps guarda quién fue).
- Login con Keycloak/AD (hoy usuarios locales) y cambio de clave por el propio usuario.
- Buscador con modelo de lenguaje; más controles de política (puertos, SSH, CIS con OpenSCAP) y remediación en lote.
- Recolección de equipos de red (vyos/frr con network_cli) y de la nube (inventario dinámico GCP).
- Conector de tickets Jira/FootPrints además de ServiceNow; facturación mensual exportable.
