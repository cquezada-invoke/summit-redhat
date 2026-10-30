# Manifiestos de arquitectura (segmentos de negocio)

Cada archivo `.yml` describe la arquitectura aprobada de un segmento de negocio (una aplicación con todos sus
componentes). Arquitectura lo propone por Pull Request; al aprobarse y desplegarse, FaceOps lo carga como versión
aprobada, arma el segmento, lo compara con el inventario y propone los tickets para cerrar las diferencias.

También se puede pegar o subir en FaceOps › Segmentos de negocio › «Nuevo segmento desde arquitectura» (queda como
propuesta hasta que un aprobador la apruebe).

## Formato

```yaml
segmento: App de pagos            # nombre del segmento de negocio
codigo: pagos                     # corto (2-8): nombra los servidores nuevos srv-<codigo>-<componente>-NN
version: 1
descripcion: "…"
responsable: Arquitectura Invoke
centro_de_datos: Centro de datos Santiago   # o Cloud GCP (dónde se crean los servidores que falten)
ambientes: [produccion, qa, desarrollo]

ficha:                            # propiedad: quién es dueño y cuánto importa (obligatorios los 4 primeros)
  area_negocio: Finanzas · Medios de pago
  dueno: Gerencia de Medios de Pago
  responsable_tecnico: Jefatura de Desarrollo de Pagos
  criticidad: alta                # alta | media | baja
  soporte: Managed Service Invoke (N2)
  horario_servicio: 24x7
  ventana_mantencion: "Domingo 02:00 a 05:00"
  rto: 1 hora
  rpo: 15 minutos
  datos: "Datos de pago (PCI DSS)"   # clasificación de la información
  usuarios: Clientes y ejecutivos comerciales

entrada:                          # por dónde arranca el flujo (y cómo se prueba de punta a punta)
  desde: usuarios                 # un externo
  hacia: web                      # un componente
  puerto: 80
  url: http://192.168.122.11/app-jboss/     # opcional: FaceOps la llama en la prueba
  espera: "Base de datos: conectada"        # opcional: texto que debe venir en la respuesta

externos:                         # actores fuera del segmento (usuarios, otros sistemas)
  - {id: usuarios, nombre: Usuarios de la app}

componentes:
  - id: app                       # minúsculas, sin espacios
    nombre: JBoss EAP
    producto: jboss               # = skill (el producto concreto)
    sistema: rhel9                # rhel9 | ubuntu2404 | windows2022
    replicas: {produccion: 2, qa: 1, desarrollo: 1}   # o un número para todos los ambientes
    recursos: {cpu: 2, ram_gb: 4, disco_gb: 20}       # por servidor
    equipos: [nodo-jboss, nodo-jboss2]                # opcional: equipos existentes que ya cumplen el rol
    uso: dedicado                 # dedicado (paga sus servidores completos) | compartido (máquinas de otro segmento: paga solo sus recursos)

# Servicios de red del camino: tipo firewall | waf | balanceador | proxy | router. Sin «sistema» son appliances
# (se asignan a equipos existentes o se provisionan aparte); con «sistema» y «producto» (p. ej. Nginx sobre RHEL)
# son servidores y se crean como cualquier otro.
#  - {id: waf, nombre: WAF, tipo: waf, replicas: 1, uso: compartido, equipos: [fgt]}

flujos:                           # en orden, desde la entrada hacia adentro; «via» = servicios de red por los que pasa
  - {desde: usuarios, hacia: web, puerto: 443, via: [fw, waf], motivo: "entrada por firewall y WAF"}
  - {desde: app, hacia: bd, puerto: 5432, motivo: "base de datos", latencia_max_ms: 5}
  - {desde: app, hacia: mensajeria, puerto: [61616, 6379], motivo: "colas y caché"}
```

Reglas de los flujos: de cada equipo de origen hacia los equipos de destino del mismo ambiente. Los ambientes no se
mezclan: cada uno (producción, QA, desarrollo) declara sus réplicas y es completo; lo que falte se propone crear.

## Costo de infraestructura

FaceOps valoriza cada segmento con las tarifas de `datos/costos.yml` (referenciales, por centro de datos):
recursos declarados (vCPU, RAM, disco) más, si el componente es dedicado, el costo fijo de cada servidor
(licencia del sistema, base del centro de datos y operación del Managed Service). Muestra el costo mensual y anual,
y la inversión incremental: lo que cuesta crear los servidores que todavía faltan.

## Solicitud de un segmento nuevo

Un segmento nuevo se pide con un ticket («Solicitar nuevo segmento de negocio») con el manifiesto adjunto. Pasa por
doble aprobación en AAP: primero Arquitectura (la solución) y luego Infraestructura, que decide por componente si se
arma un parque nuevo o se asigna a máquinas existentes que ya tienen el producto. Al aprobarse, el manifiesto final
(con esas decisiones) queda en Git (Gitea `invoke/arquitectura`) y el segmento se activa.
