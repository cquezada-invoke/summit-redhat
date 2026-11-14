# Backlog por etapa de liberación de FaceOps

Lo pedido para funcionalidades que no están en la etapa en certificación queda aquí hasta que se libere su etapa
(regla del Plan de cierre: un módulo congelado no recibe funcionalidades nuevas). Etapas en `faceops/datos/etapas.yml`.

## Etapa 11 · FaceOps Agent (número provisorio)
Sale de la Etapa 1 por decisión de Cristian (13-11-2026): requiere el agente de respaldo («respaldo-git») en la máquina.
En FaceOps existe UN SOLO agente en las máquinas, llamado «FaceOps Agent» (decisión de Cristian, 14-11-2026).
Alcance actual: notificar cambios. El resto de sus funcionalidades (historial de cambios, puntos de restauración,
recuperación, restauración, etc.) se implementa de a poco en esta fase. En la ficha solo se muestran si la
máquina tiene el agente; dentro de las pantallas de la Etapa 1 quedan bloqueados con la glosa «Etapa 11 · requiere
FaceOps Agent · aún no liberada».

Pendiente para cuando se libere:
- Historial de cambios: mostrar solo los últimos 10, con paginador y buscador. Ya está en la plantilla común
  (`equipo.html`, 10 por página, buscador por archivo, paquete, usuario, ticket o commit); falta certificarlo con la etapa.
- Puntos de restauración: máximo 10 por página con paginador. Ya está en la plantilla común; falta certificarlo.
- Plan de prueba de recuperación sin agente (antes C11 de «Sistema · Inventariar máquina»): se certifica con
  «FaceOps · Recuperar servidor» (C2 y C4).
- Asignar el número definitivo de la etapa en el Plan de cierre.

## Etapa 2 · Monitoreo, salud y alertas
- Eventos del BMC (iBMC/iDRAC/iLO) en la ficha del nodo físico: quitados de la ficha en la Etapa 1 (13-11-2026); quedan en
  `etapas.yml` (bloque `#hw-eventos-bmc`, Etapa 2) y se reponen al liberar el Monitoreo.
