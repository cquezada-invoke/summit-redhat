# ambientes · manifiesto de cada ambiente

`produccion.yml` dice qué combinación exacta está en producción: versión del `.war` (registro de paquetes de Gitea),
commit de `jboss-config` y commit de la base del SO de cada servidor (`estado-servidores`, rama master).

- Lo actualiza AAP solo en cada despliegue y en cada cambio de configuración: la historia de este repo es la historia de producción.
- Para volver a un estado anterior: plantilla AAP **"JBoss · Restablecer a versión"** con el commit (o tag) de este repo.
