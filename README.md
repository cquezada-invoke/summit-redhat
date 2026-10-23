# summit-redhat

Código de las demos de Ansible del stand de Invoke en Red Hat Summit. Todo corre en la VM `demo-ansible-summit` (GCP) y sus nodos KVM anidados.

| Carpeta | Contenido |
|---|---|
| `demo-drift/` | Demo 1 · Drift y visitantes: panel web, playbooks del estado base, alta de visitantes, simulador. |
| `demo-cicd/` | Demo 2 · CI/CD a JBoss y Demo 3 · Plataforma como código: Tekton, app `app-jboss`, configuración de AAP, Vault y plataforma (PostgreSQL, Valkey, Artemis, Keycloak). |
| `demo-summit-repo/` | Proyecto de AAP: playbooks que ejecuta el controller (JBoss, plataforma, autoservicio). |
| `docs/diagramas/` | Diagramas interactivos del ecosistema y de la Demo 4 de redes. |

## Uso en la VM

```bash
git clone https://github.com/cquezada-invoke/summit-redhat ~/summit-redhat   # primera vez
cd ~/summit-redhat && git pull                                              # para actualizar
```

Luego se copian o sincronizan las carpetas a su ruta de trabajo (`~/demo-drift`, `~/demo-cicd`, `~/demo-summit-repo`).

Las claves no están en este repositorio: viven en Vault y en archivos locales de la VM.
