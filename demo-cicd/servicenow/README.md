# ServiceNow · Demo 3

Cada operación de plataforma que ejecuta Ansible queda registrada sola en ServiceNow:
un **cambio estándar** (preaprobado) por ejecución y una **tarea** por operación real (script SQL,
cola, rol o realm de Keycloak, caché, datasource). La tarea se abre "En curso" antes de la operación y se cierra
al terminar con la duración real, el enlace al job de AAP y el tiempo manual estimado
(`plataforma/tiempos_manuales.yml`). Si no hay cambios, no se abre nada. Si ServiceNow no responde, el job solo avisa.

- Lógica: `demo-summit-repo/plataforma/tareas/snow.yml` (llamada desde `aplicar.yml`).
- Credencial de AAP: "ServiceNow · ansible.automatizacion" (tipo "ServiceNow · instancia"), con datos desde Vault `secret/servicenow`.
- Configuración usada: `configurar_servicenow.py` (grupo y campos), `reportes_servicenow.py` (reportes), `credencial_aap.sh`.
- Instancia: https://dev459364.service-now.com · usuario técnico `ansible.automatizacion`.
