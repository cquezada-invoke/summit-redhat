# Demo 6 · Nube (GCP)

La misma AAP del Summit gobierna un proyecto de Google Cloud aparte: **invoke-summit-cloud**. Todo lo que Ansible crea lleva la etiqueta `demo=summit-cloud` y el prefijo `summit-cloud-`; el resto del proyecto solo se **lee** (reportes). La VM del Summit (proyecto master-ia-320218) no se toca y no se abre ningún puerto hacia ella.

| Caso | Qué se muestra | Cómo se dispara |
|---|---|---|
| 1. VM de autoservicio | Ítem "Solicitar VM en la nube" en ServiceNow → EDA → workflow con aprobación → VM RHEL 9 endurecida, con node_exporter, en Prometheus y en el inventario "Nube GCP" → ticket cerrado con la IP. "Dar de baja VM en la nube" la borra. | Ticket en ServiceNow |
| 2. Huérfanos y costos | Discos sin usar, IPs reservadas sin uso, snapshots de más de 30 días y VMs encendidas fuera de horario, con costo mensual estimado → incidente y correo → aprobación → limpia solo lo etiquetado. | Lunes 08:00 (o a mano) |
| 3. Reglas peligrosas | Reglas con 0.0.0.0/0 en 22 o 3389: las `summit-cloud-*` se restringen a los rangos aprobados en Git; las ajenas solo se informan → incidente y correo. | Cada 10 min |

## Piezas

| Ruta | Para qué |
|---|---|
| `nube/` | Contenido del repositorio `invoke/nube` en Gitea: `playbooks/`, `rulebooks/nube.yml` (webhook :5004), `datos/` (proyecto, tamaños, precios, rangos aprobados), `inventario/gcp.yml` (gcp_compute filtrado por etiqueta). |
| `configurar_nube.py` | Idempotente. Llave SSH en Vault (`secret/nube/ssh`), Gitea, organización "Nube" en AAP (proyecto, inventario, credenciales, plantillas, workflows, notificador "Correo · nube", programaciones), EDA, catálogo de ServiceNow, vigía, exportador, Prometheus y Grafana. |
| `servicenow/` | `configurar_catalogo_nube.py` (categoría "Nube" y sus dos ítems) y `vigia_nube.py` (entrega los tickets a EDA). |
| `monitoreo/` | `exportador_nube.py` (:9420: lo que AAP hace en GCP + file_sd de las VMs), trabajos de Prometheus y tablero "Summit · Nube". |
| `herramientas/` | `probar-nube.py` (ticket de punta a punta), `reiniciar-nube.sh` (borra los recursos de prueba), `estado-nube.sh`. |

Plantillas en AAP (organización Nube): "Nube · Solicitar VM con aprobación" (workflow), "Nube · Dar de baja VM", "Nube · Reporte de huérfanos con limpieza" (workflow), "Nube · Vigilar firewall", "Nube · Prueba: crear huérfanos", "Nube · Prueba: regla peligrosa" y "Nube · Avisar por correo" (la usan las otras).

## Conectar la nube (una vez, lo hace Cristian)

Ver `docs/pendientes.md` → "Demo 6 · conectar GCP". En resumen: habilitar Compute Engine API, crear la cuenta de servicio con roles mínimos, guardar su llave JSON en Vault (`secret/gcp`, campo `llave`), crear el presupuesto con alerta y pegar la clave de aplicación de Gmail en "Correo · nube". Después:

```
python3 ~/summit-redhat/demo6-nube/configurar_nube.py
```

Crea la credencial "GCP · invoke-summit-cloud", la fuente de inventario y activa las programaciones. Sin la llave, todo lo demás funciona y los tickets se cierran con "la nube todavía no está conectada".

## Cómo hacer la demo

1. Antes: `bash ~/summit-redhat/demo6-nube/herramientas/reiniciar-nube.sh` y abrir el tablero http://34.176.42.56:8090/grafana/d/summit-nube.
2. **Caso 1:** en ServiceNow, Service Catalog → Nube → "Solicitar VM en la nube" (nombre `ventas-api`, tamaño S). En AAP aparece el workflow lanzado por EDA; el paso "preparar" muestra el plan; aprobar en Workflow Approvals. En unos 4 minutos el ticket queda cerrado con la IP, la VM aparece en el tablero y en el inventario "Nube GCP". Para terminar: "Dar de baja VM en la nube" con el mismo nombre.
   Desde la VM: `python3 ~/summit-redhat/demo6-nube/herramientas/probar-nube.py vm nombre=ventas-api tamano=S --limpiar`
3. **Caso 2:** lanzar "Nube · Prueba: crear huérfanos" y luego "Nube · Reporte de huérfanos con limpieza": incidente con la lista y el costo, correo, aprobación y limpieza (solo lo etiquetado).
4. **Caso 3:** lanzar "Nube · Prueba: regla peligrosa" (abre 22 y 3389 a internet sobre una etiqueta sin VMs). En 10 minutos como máximo, o lanzando "Nube · Vigilar firewall", la regla queda restringida a los rangos de `datos/firewall-aprobados.yml`, con incidente y correo.
5. Al terminar: `reiniciar-nube.sh` y, si se quieren borrar los incidentes de la demo, `probar-nube.py incidentes`.

## Seguridad y costos

- Máquinas mínimas (e2-small / e2-medium, 20 GB) y se borran después de la demo. Presupuesto con alerta en el proyecto.
- Las VMs nuevas solo aceptan SSH y métricas desde 34.176.42.56/32 (regla `summit-cloud-acceso-aap`), sin contraseñas ni root por SSH.
- La limpieza y la corrección de reglas actúan solo sobre lo etiquetado `demo=summit-cloud` o con prefijo `summit-cloud-`, y vuelven a comprobarlo antes de tocar nada.
