# Pulsos de uso de FaceOps

Cada instancia de FaceOps deja un **pulso** por request HTTP y por evento de negocio, y lo envía a FaceAdmin, que lo guarda en su
data lake. El objetivo es mirar el comportamiento de la herramienta (uso, errores, latencia) y, más adelante, analizarlo con IA.

## Flujo

1. `faceops/app/pulsos.py` (registrado en `create_app`) arma el pulso en `after_request` (con la excepción, si la hubo) y en los eventos.
2. Un hilo lo pasa cada 5 s a una cola en disco (`/var/lib/faceops/pulsos/cola.db`, SQLite) y cada 45 s la envía por lotes
   (hasta 5.000 líneas, NDJSON con gzip) a `POST /api/instancia/pulsos` de FaceAdmin, con la llave de la instancia (`X-FaceAdmin-Token`).
   Si FaceAdmin no responde, la cola espera al próximo ciclo; la app nunca espera. Tope de la cola: 50 MB (se descarta lo más antiguo).
3. FaceAdmin (`faceadmin/pulsos.py`) valida la llave, vuelve a limpiar cada línea (solo campos del esquema, redacción de secretos),
   fija `instancia` con el código del cliente dueño de la llave y escribe:

   `s3://faceops-pulsos/instancia=<código>/fecha=YYYY-MM-DD/hora=HH/<lote>.ndjson.gz` (fecha y hora UTC del pulso)

   El nombre del lote es `<id-inicial>-<id-final>-<hash>`: reenviar el mismo lote sobrescribe el mismo objeto (idempotente).
4. La vista **Producto › Uso de FaceOps** (`/fabrica/uso-faceops`) lee el lake y guarda un agregado por instancia y día
   (tabla `pulsos_dia`), que se recalcula solo si cambian los objetos del día.

El lake es un MinIO en la VM de FaceAdmin (`herramientas/faceadmin/instalar-minio-faceadmin.sh`; solo escucha en 127.0.0.1,
credenciales en `/etc/faceadmin/s3.env`, modo 600). Sin MinIO configurado, FaceAdmin usa la misma estructura en
`/var/lib/faceadmin/lake/`. Retención: `FACEADMIN_PULSOS_RETENCION_DIAS` (180), aplicada con regla de ciclo de vida del bucket y una
purga diaria de FaceAdmin.

## Configuración

| Variable | Dónde | Por defecto | Uso |
|---|---|---|---|
| `FACEOPS_PULSOS` | FaceOps | `1` | `0` apaga los pulsos |
| `FACEOPS_PULSOS_CADA` | FaceOps | `45` | segundos entre envíos (mínimo 10) |
| `FACEOPS_PULSOS_MAX_MB` | FaceOps | `50` | tope de la cola local |
| `FACEADMIN_S3_URL`, `FACEADMIN_S3_ACCESS`, `FACEADMIN_S3_SECRET` | FaceAdmin (`/etc/faceadmin/s3.env`) | — | MinIO del lake |
| `FACEADMIN_PULSOS_BUCKET` | FaceAdmin | `faceops-pulsos` | bucket |
| `FACEADMIN_PULSOS_RETENCION_DIAS` | FaceAdmin | `180` | retención |

## Esquema del pulso (versión `v` = 1)

Una línea JSON por pulso. Campos comunes:

| Campo | Tipo | Descripción |
|---|---|---|
| `v` | int | versión del esquema |
| `ts` | string | instante UTC ISO 8601 con milisegundos (`2026-11-11T14:05:00.123Z`) |
| `recibido` | string | instante UTC en que FaceAdmin lo guardó |
| `instancia` | string | código del cliente en FaceAdmin dueño de la llave (fuente de verdad) |
| `instancia_faceops` | string | nombre que la instancia se da a sí misma (`FACEOPS_INSTANCIA`) |
| `instancia_id` | string | huella `FOP-…` de la instancia |
| `tipo` | string | `request` o `evento` |
| `cliente` | string | código del cliente activo en la consola (multicliente) |
| `usuario_id` | int | id interno del usuario en esa instancia (nunca el correo ni el nombre) |
| `rol` | string | `admin`, o el modo del perfil (`escritura`, `lectura`, `ejecutivo`…) |
| `ver_como` | bool | un administrador navegaba «como otro usuario» |

Solo `tipo = request`:

| Campo | Tipo | Descripción |
|---|---|---|
| `metodo` | string | GET, POST… |
| `ruta` | string | plantilla de la ruta (`/lab/inventario/<eid>`): agrupar por esto |
| `ruta_real` | string | ruta efectiva; parámetros y segmentos sensibles como `[redactado]` |
| `endpoint` | string | vista Flask (`consola.inventario`) |
| `modulo` / `seccion` | string | primer segmento de la ruta / nombre de la vista (sección del menú) |
| `status` | int | código HTTP |
| `ms` | float | duración en el servidor |
| `bytes_resp` / `bytes_req` | int | tamaño de la respuesta (sin comprimir) y del cuerpo recibido |
| `ua` | string | navegador resumido (`Chrome 131 · macOS`, `curl`, `kube-probe`…) |
| `ip` | string | IP enmascarada (`/24` en IPv4, `/48` en IPv6) |
| `sesion` | string | hash (16 hex) de la sesión: une los requests de una misma visita sin identificarla |
| `xhr` | bool | llamada de la página (fetch/JSON), no navegación |
| `error` | objeto | si hubo excepción: `tipo`, `mensaje` (redactado, 300 car.), `traza` (últimos 8 marcos `archivo:línea función`); en 4xx/5xx sin excepción, `tipo = "HTTP <status>"` |

Solo `tipo = evento` (`evento` + `datos`):

| `evento` | `datos` |
|---|---|
| `login` | `paso` (`clave` o `codigo`) |
| `login_doble_factor_pedido` | — |
| `login_fallido` | `status` |
| `logout` | `usuario_id` |
| `ticket_creado` | `ticket_id`, `numero`, `automatizacion_id`, `estado`, `endpoint` |
| `ticket_aprobado` / `ticket_rechazado` | `ticket_id`, `por_usuario_id` |
| `error_aap` | `mensaje` (redactado; el mismo mensaje se informa a lo más cada 5 min) |

## Lo que nunca viaja

Cuerpos de formularios, contraseñas, tokens, cookies, cabeceras `Authorization`, correos, valores de parámetros cuyo nombre
contenga `clave`, `pass`, `token`, `secret`, `key`, `llave`, `auth`, `csrf`, `otp`…, ni segmentos de ruta con forma de token (24 o más
caracteres). Los mensajes de error pasan por una redacción (Bearer/Basic, `clave=…`, credenciales en URL, cadenas largas, correos)
en FaceOps y de nuevo en FaceAdmin. Excluidos: estáticos, `/version` y sondeos de salud.

## Para el análisis con IA

- Particiones Hive (`instancia=`, `fecha=`, `hora=`): se pueden montar como tabla externa (Trino/Hive, formato JSON) igual que el
  data lake de las apps (`demo-datalake`).
- Preguntas típicas: rutas más lentas por p95 y su evolución; errores nuevos (por `error.tipo` + `ruta`) tras una versión
  (`instancia_id` + la versión que informa `/api/instancia/uso`); recorridos por `sesion` (embudos: login → ticket_creado →
  ticket_aprobado); módulos sin uso; usuarios/roles que más fallan; picos por hora.
- Un mismo pulso puede llegar en dos lotes si un envío se cortó tras guardarse: deduplicar por la línea sin `recibido`.
