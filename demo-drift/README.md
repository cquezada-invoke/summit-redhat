# Demo 1 · Drift y visitantes

El estado correcto de los servidores vive en Git (Gitea `invoke/estado-servidores`): rama `master` = base aprobada
(solo cambia por Pull Request), rama `main` = historial de fotos de cada servidor. El panel (https://34.176.42.56:9443)
muestra cada cambio manual y, según el nivel del servidor, lo corrige con Ansible o solo lo registra en Git.

## Control de paquetes (versión exacta)

- Base: `master:paquetes/<servidor>.txt` (nombre-versión-release.arquitectura). Se aprueba con **Proponer como base**.
- El vigía de cada nodo detecta toda transacción de dnf/rpm (instalar, desinstalar, cambiar versión) con quién y qué comando.
- **Corregir:** AAP deja los paquetes iguales a la base (desinstala lo que sobra, reinstala lo que falta y vuelve a la versión
  exacta). Si una versión ya no está en los repositorios, queda en el incidente como "versión no disponible".
- **Solo registrar:** el cambio queda como commit en `main`.
- **Propagar base** con "Paquetes": instala en los destinos los paquetes aprobados del servidor de referencia que les falten,
  y actualiza la base de paquetes de esos destinos por Pull Request.
- **Volver a un commit:** botón del panel o plantilla AAP **"Estado · Volver a un commit"** (encuesta: servidor, commit o
  `base`, y qué restablecer: paquetes y/o archivos). Deja el servidor igual a ese commit y lo registra en Git.
- Kernels y llaves gpg no se concilian (pueden convivir varias versiones).

Prueba (en la VM):
```
bash ~/demo-drift/herramientas/probar-paquetes.sh instalar   # instala "tree" en nodo-so como operador
bash ~/demo-drift/herramientas/probar-paquetes.sh estado     # ¿sigue instalado?
bash ~/demo-drift/herramientas/probar-paquetes.sh quitar     # lo quita a mano (si el servidor está en "solo registrar")
```
Configuración (idempotente): `python3 ~/demo-drift/configurar_paquetes.py`.

## Servidores bajo gestión

nodo-so, nodo-nginx, nodo-postgres, nodo-tomcat, nodo-jboss, **nodo-jboss2** (192.168.122.18) y **nodo-windows**
(192.168.122.17). nodo-jboss2 tiene vigía, auditd, base de paquetes en `master` y foto inicial, igual que los demás.
Los archivos de configuración de JBoss los sigue controlando `jboss-config` (drift cada 5 min); el vigía de la Demo 1
no los corrige.

## Windows (nodo-windows)

- Qué se controla: servicios críticos **W3SVC** (IIS) y **W32Time** (estado e inicio automático) y los **miembros del grupo
  Administradores**.
- Base aprobada: `master:nodo-windows/windows.yml` (cambia por Pull Request; **Proponer como base** funciona igual).
  Fotos: `main:nodo-windows/windows.yml`.
- Vigía: tarea programada **"Summit Drift Vigia"** (SYSTEM, al iniciar) con `C:\ProgramData\SummitDrift\vigia-windows.ps1`;
  revisa cada 15 s y avisa al panel quién hizo el cambio (auditoría de grupos 4732/4733 o eventos de servicios).
- AAP: plantilla **"Drift · Windows"** (`drift/windows.yml`, inventario "Servidores Windows", credenciales WinRM y Gitea).
  Modos: incorporar, retirar, corregir, alertar, foto y proponer. La lanza el panel.
- **Corregir:** Ansible deja servicios y Administradores iguales a la base; el incidente muestra el antes/después (YAML).
  **Solo registrar:** el cambio queda como commit en `main`. "Propagar base" y "Volver a un commit" no aplican a Windows.

Prueba (en la VM; no crea usuarios: usa la cuenta integrada Guest, deshabilitada):
```
bash ~/demo-drift/herramientas/probar-windows.sh admin      # agrega Guest a Administradores
bash ~/demo-drift/herramientas/probar-windows.sh servicio   # W3SVC pasa a inicio manual (IIS sigue arriba)
bash ~/demo-drift/herramientas/probar-windows.sh hora       # detiene W32Time
bash ~/demo-drift/herramientas/probar-windows.sh limpiar    # deja nodo-windows igual a la base (sin incidente)
bash ~/demo-drift/herramientas/probar-windows.sh estado
```
Configuración (idempotente): `python3 ~/demo-drift/configurar_windows.py [corregir|registrar|alertar]`.
Revertir: botón **Quitar** de nodo-windows en el panel (borra la tarea programada y la carpeta del vigía).
