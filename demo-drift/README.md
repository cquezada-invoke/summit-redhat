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
