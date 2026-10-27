# Pendientes de las demos del Summit

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

## Demo 4: actualización masiva de "firmware" en parque mixto (en cola, después de OpenShift)
- Un cambio en ServiceNow con aprobación en AAP; una tarea por equipo.
- Routers de sucursal FRR (3-4, nuevos en containerlab) 9.1 → 10.2; firewall VyOS; switches Arista cEOS
  (falta una segunda versión de cEOS: la descarga Cristian); servidores RHEL de las demos (parches + reinicio).
- Por tipo y en lotes: canario de cada tipo, luego el resto; pre/post-chequeo (versión, BGP, rutas, servicios);
  si falla, vuelve a la versión anterior y se detiene. Caso de falla a propósito para la demo en vivo.
- Actualizar el ecosistema con los equipos nuevos y los comandos.

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
