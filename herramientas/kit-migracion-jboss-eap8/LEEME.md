# Kit · Migración de JBoss 5, 6 y 7 a JBoss EAP 8

Es la misma automatización que FaceOps lanza en AAP («Migración · JBoss a EAP 8»), para correrla con `ansible-playbook`
desde tu equipo. El procedimiento completo está en el manual de uso.

    ./ejecutar.sh evaluar srv-legado-jboss5-01            # informe en informes/srv-legado-jboss5-01/evaluacion.md
    ./ejecutar.sh migrar  srv-legado-jboss5-01 nodo-jboss # transforma, despliega en EAP 8 y verifica

Contenido: `migrar-jboss-eap8.yml` (playbook) · `files/` (análisis y ajuste de descriptores) · `inventario.ini` ·
`ansible.cfg` · `ejecutar.sh` (atajo) · `informes/` (aquí quedan las evaluaciones).
