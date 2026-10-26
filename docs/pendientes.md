# Pendientes de las demos del Summit

## Manejo de excepciones (decidido por Cristian el 26-10-2026)
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

## Demo 4: más de un puerto por ticket (pedido por Cristian el 26-10-2026)
- Aceptar en el campo Puerto una lista o rango ("8080, 8443" o "8000-8010").
- VyOS: un port-group con todos los puertos y una sola regla. firewalld: una rich rule por puerto.
- Aprobación con todos los comandos juntos; prueba de cada puerto; el ticket se cierra solo si todos responden.

## Demo 4: preparar red para clúster OpenShift (pedido por Cristian el 26-10-2026, va después del bloqueo de IP)
- Ítem de catálogo "Preparar red para clúster OpenShift" (nombre del clúster, cantidad de nodos) → aprobación en AAP.
- Ansible: asigna segmento desde un rango reservado; router: interfaz/VLAN y anuncio BGP (vía commit en Gitea);
  firewall: 6443, 22623, 80/443; DNS del laboratorio (agregar): api, api-int y *.apps.
- Prueba y cierre del ticket con los datos para el instalador.

## Otros
- CMDB en ServiceNow alimentada por Ansible (registra los servidores reales); es opcional para la demo.
- Catálogo: categoría "Infraestructura" con "Servidores virtuales" ("Solicitar servidor RHEL", con tamaño,
  costo y aprobación para el tamaño L) y "Red". Ocultar las categorías de ejemplo.
- ServiceNow: desactivar las tareas automáticas del modelo de cambio ("Implement" y "Post implementation
  testing") en los cambios de Ansible, y cerrar los cambios de prueba CHG0030001 a CHG0030007.
- ServiceNow: eliminar los registros de ejemplo que trae la instancia (pedido por Cristian el 26-10-2026):
  RITM0000001 a RITM0000005 y sus REQ, y los incidentes, cambios y tareas de ejemplo. Conservar solo lo
  creado para las demos (ítem "Solicitar acceso de red", RITM0010xxx, CHG00300xx). Listar antes de borrar.
- Planilla: revisión completa hoja por hoja (restaurar lo perdido por la sincronización, URL por IP,
  agregar ServiceNow, VyOS, ticket a EDA a AAP, Demo 5 y la guía de ServiceNow).
- Enlaces de AAP que salen con la IP interna 10.194.0.2.
- FortiGate: pedir licencia NFR a Fortinet; el disco y la topología quedan listos para reactivarlo.
- Mirror de Gitea `invoke/automatizacion` hacia GitHub (falta un token de GitHub).
