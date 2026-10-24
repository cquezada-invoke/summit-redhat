# red-laboratorio

Estado deseado de la red del laboratorio de la Demo 4. AAP lee este repositorio.

- `config/<equipo>.conf`: configuración declarada de cada equipo (FRR). Cambiarla aquí es la única forma válida de cambiar la red.
- `playbooks/aplicar.yml`: lleva los equipos al estado de Git ("Red · Aplicar configuración").
- `playbooks/drift.yml`: detecta cambios manuales y los revierte ("Red · Verificar drift", programada cada 10 minutos).
- `playbooks/solicitar-acceso.yml`: regla en el FortiGate pedida por la app (fase 2).
