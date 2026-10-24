# Demo 5 · Autorremediación

Un vigía en nodo-jboss observa el servicio, la app y el directorio de logs y avisa a **Event-Driven Ansible**.
EDA aplica la **política de riesgo** guardada en Git (`invoke/operacion` → `politicas/riesgo.yml`):

| Caso | Riesgo | Qué hace AAP |
|---|---|---|
| JBoss se detiene | bajo | "Remediación · Reiniciar JBoss": diagnóstico, reinicio y validación, sin aprobación |
| Logs llenos | bajo | "Remediación · Limpiar logs": borra lo que `politicas/logs.yml` permite y confirma el espacio |
| La app responde con error | alto | Workflow "Remediación · Rollback con aprobación": diagnóstico → **aprobación en AAP** → vuelta a la última versión validada (si se rechaza o vence: escalar sin tocar producción) |

| Ruta | Para qué |
|---|---|
| `instalar-demo5.sh` | Puerto 5001 en la red de los nodos, prepara nodo-jboss y configura Gitea, AAP y EDA |
| `configurar_remediacion.py` | Repo `invoke/operacion`, organización "Operación", plantillas, workflow y activación de EDA |
| `preparar-nodo.yml` | Logs de la app en un tmpfs de 200 MB (no puede llenar el disco real) y el vigía |
| `nodo/` | Vigía (`vigia-jboss.py`) y su servicio |
| `operacion/` | Contenido del repo de Gitea: políticas, playbooks y rulebook |
| `herramientas/` | `romper-jboss.sh`, `llenar-logs.sh`, `romper-despliegue.sh`, `reiniciar-demo5.sh` |
