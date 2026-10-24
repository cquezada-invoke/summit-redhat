# Demo 4 · Redes

Laboratorio de red emulado con containerlab dentro de la VM, gestionado por la misma AAP.

**Fase 1 (sin imágenes con licencia):** ISP simulado y router de borde con FRR (BGP entre ellos), `srv-app` (HTTP :8080) y `pc-usuario`. Caso 2, configuración como código con detección de drift, funcional sobre el router.

**Fase 2:** Arista cEOS (núcleo y dos switches) y FortiGate de evaluación; caso 1, regla de firewall pedida por la app. Ya están preparados en la topología y en `red-laboratorio/playbooks/solicitar-acceso.yml`.

| Ruta | Para qué |
|---|---|
| `instalar-lab.sh` | Instala containerlab, construye la imagen FRR con SSH, despliega el laboratorio y configura Gitea, Vault y AAP. |
| `configurar_red_aap.py` | Repositorio `invoke/red-laboratorio` en Gitea, `secret/red/ssh` en Vault y, en AAP, la organización "Redes" con su inventario, credencial, proyecto y plantillas. |
| `lab/` | Topología `summit-red.clab.yml` y configuraciones base. |
| `red-laboratorio/` | Contenido inicial del repositorio de red: `config/rtr.conf` es el estado deseado y `playbooks/` lo que ejecuta AAP. |
| `herramientas/` | `probar-red.sh`, `romper-red.sh`, `consola-rtr.sh`, `reiniciar-lab.sh`. |

Direcciones de gestión: isp .10 · rtr .11 · fgt .12 · core .13 · swa .14 · swb .15 · srv-app .21 · pc-usuario .22 (172.20.20.0/24).
