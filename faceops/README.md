# FaceOps v1

Consola del Managed Service de Invoke sobre Red Hat Ansible Automation Platform.

- **Consola** (`/faceops/`): panel, buscador por intención, automatizaciones (desde AAP, con ficha común y formulario),
  tickets con trazabilidad y aprobación, inventario inteligente (recolector diario en AAP), gobierno (segmentos,
  skills, políticas de seguridad) y mapa del ecosistema.
- **FaceOps-admin** (`/faceops/admin/`): clientes (AAP y herramienta de tickets de cada uno), usuarios y perfiles
  (cliente × segmentos × lectura/escritura × aprobador), catálogo y tarifas, gobierno como YAML e ingresos por servicio.

Despliegue en la VM del stand: `bash faceops/desplegar.sh` · claves iniciales: `sudo cat /var/lib/faceops/credenciales-iniciales.txt`.
