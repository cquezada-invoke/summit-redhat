# DEMO 5 · Autorremediación

| Ruta | Qué es |
|---|---|
| `politicas/riesgo.yml` | Qué se corrige solo (riesgo bajo) y qué espera aprobación (riesgo alto) |
| `politicas/logs.yml` | Retención de logs: cuántos días se conservan y el umbral de aviso |
| `rulebooks/remediacion-jboss.yml` | Regla de Event-Driven Ansible: evento del vigía → plantilla o workflow |
| `playbooks/` | Lo que ejecuta AAP: reiniciar JBoss, limpiar logs, diagnosticar, volver a la versión validada, escalar |

Cambiar una política aquí (por ejemplo `retener_dias`) cambia lo que hace la remediación en el próximo evento.
