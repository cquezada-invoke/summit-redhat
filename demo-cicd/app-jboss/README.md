# App de pagos · JBoss EAP 8

Cada push a `main` dispara el pipeline de Tekton: secretos, build y pruebas, vulnerabilidades,
publicación del `.war` y despliegue con Ansible Automation Platform.

Para la demo: cambia el `mensaje` en `src/main/resources/app.properties` y haz push (aparece bajo el título de la app).

## Qué hace

Login con usuarios de demo y, dentro: resumen de la cuenta (saldo y últimos movimientos), pagar a un comercio
(tarjeta, QR o transferencia), transferir a otra cuenta, historial con filtros, reembolsos (pedir y ver el estado)
y una vista de administración para el rol `supervisor` (pagos del día por rubro, medio y canal; aprobar o rechazar reembolsos).

La interfaz es HTML + JS (`src/main/webapp/static`) y toda operación va por HTTP/JSON al backend, así el espejo del
balanceador al data lake recibe requests de negocio con rubro, región, medio, monto y canal (`web` o `app` en móvil).

## Usuarios de demo

Están en `src/main/resources/bd/usuarios-demo.properties` (usuario, clave de demo, rol, región, saldo y cuenta).
Son ficticios. La app los crea al primer uso de la base y guarda solo el hash PBKDF2 de la clave.

## Base de datos

La app aplica su estructura sola (`src/main/resources/bd/esquema.sql`, idempotente con `IF NOT EXISTS`) la primera
vez que usa la base, con un candado para que los JBoss no choquen. La tabla `pedidos` de `db/V1__pedidos.sql` no cambia.
Tablas: `app_usuarios`, `app_cuentas`, `app_comercios`, `app_movimientos`, `app_reembolsos`, `app_sesiones`.

La sesión es una cookie `pagos_sesion` (HttpOnly) cuyo SHA-256 se guarda en `app_sesiones`, así funciona con el
balanceo entre JBoss sin replicar sesiones.

## Rutas (bajo `/app-jboss`)

| Método | Ruta | Uso |
|---|---|---|
| GET | `/` | Interfaz; muestra versión, servidor y «Base de datos: conectada» |
| GET | `/salud` | `ok <versión>` (lo valida Ansible) |
| POST | `/login`, `/logout` · GET `/sesion` | Sesión |
| GET | `/saldo` | Resumen de la cuenta |
| GET | `/movimientos?desde&hasta&tipo&medio&rubro&estado&texto` | Historial |
| POST | `/pagos` · `/qr/pagar` | Pagar (tarjeta o transferencia · QR) |
| GET / POST | `/pagos/{id}` · `/pagos/{id}/confirmar` · `/pagos/{id}/anular` | Detalle, estado, anulación del día |
| POST | `/transferencias` | Transferir |
| GET / POST | `/reembolsos` · POST `/reembolsos/{id}/aprobar` o `/rechazar` | Reembolsos |
| GET | `/cuentas`, `/comercios` | Datos para los formularios |
| GET | `/admin/resumen?fecha` · `/comercios/{c}/liquidacion?fecha` | Supervisor |

Las rutas coinciden con las de la prueba de carga (`demo-datalake/colector/carga-pagos.py`); sin sesión responden 401.
