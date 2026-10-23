# App de demo · JBoss EAP 8

Cada push a `main` dispara el pipeline de Tekton: secretos, build y pruebas, vulnerabilidades,
publicación del `.war` y despliegue con Ansible Automation Platform.

Para la demo: cambia el `mensaje` en `src/main/resources/app.properties` y haz push.
