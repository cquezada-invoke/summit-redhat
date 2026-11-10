-- Estructura de la App de pagos. La aplica la propia app al primer uso de la base (idempotente: IF NOT EXISTS),
-- dentro de una transacción con candado, para que dos JBoss que arrancan juntos no choquen.
-- La tabla «pedidos» (db/V1__pedidos.sql) no se toca.
CREATE TABLE IF NOT EXISTS app_usuarios (
    id        serial PRIMARY KEY,
    usuario   text NOT NULL UNIQUE,
    nombre    text NOT NULL,
    rol       text NOT NULL DEFAULT 'cliente',
    region    text NOT NULL DEFAULT 'Metropolitana',
    hash      text NOT NULL,
    creado    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_cuentas (
    id          serial PRIMARY KEY,
    numero      text NOT NULL UNIQUE,
    usuario_id  integer NOT NULL REFERENCES app_usuarios(id),
    saldo       numeric(14, 2) NOT NULL DEFAULT 0,
    moneda      text NOT NULL DEFAULT 'USD'
);

CREATE TABLE IF NOT EXISTS app_comercios (
    codigo  text PRIMARY KEY,
    nombre  text NOT NULL,
    rubro   text NOT NULL
);

CREATE TABLE IF NOT EXISTS app_movimientos (
    id           bigserial PRIMARY KEY,
    codigo       text NOT NULL UNIQUE,
    cuenta_id    integer NOT NULL REFERENCES app_cuentas(id),
    tipo         text NOT NULL,
    medio        text,
    comercio     text,
    contraparte  text,
    rubro        text,
    region       text,
    canal        text,
    monto        numeric(14, 2) NOT NULL,
    estado       text NOT NULL,
    glosa        text,
    ref_codigo   text,
    creado       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_movimientos_cuenta ON app_movimientos (cuenta_id, creado DESC);

CREATE INDEX IF NOT EXISTS app_movimientos_creado ON app_movimientos (creado);

CREATE TABLE IF NOT EXISTS app_reembolsos (
    id           bigserial PRIMARY KEY,
    codigo       text NOT NULL UNIQUE,
    pago_codigo  text NOT NULL,
    cuenta_id    integer NOT NULL REFERENCES app_cuentas(id),
    monto        numeric(14, 2) NOT NULL,
    motivo       text,
    canal        text,
    estado       text NOT NULL DEFAULT 'solicitado',
    creado       timestamptz NOT NULL DEFAULT now(),
    resuelto     timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS app_reembolsos_vigente ON app_reembolsos (pago_codigo) WHERE estado <> 'rechazado';

CREATE TABLE IF NOT EXISTS app_sesiones (
    token_sha   text PRIMARY KEY,
    usuario_id  integer NOT NULL REFERENCES app_usuarios(id) ON DELETE CASCADE,
    canal       text,
    creado      timestamptz NOT NULL DEFAULT now(),
    expira      timestamptz NOT NULL
);

INSERT INTO app_comercios (codigo, nombre, rubro) VALUES
    ('COM-0001', 'Supermercado Los Andes', 'Supermercado'),
    ('COM-0002', 'Farmacia Central', 'Farmacia'),
    ('COM-0003', 'Estación Ruta 5', 'Combustible'),
    ('COM-0004', 'Café del Puerto', 'Restaurantes'),
    ('COM-0005', 'TecnoHogar', 'Tecnología'),
    ('COM-0006', 'Moda Austral', 'Vestuario'),
    ('COM-0007', 'Viajes Patagonia', 'Viajes'),
    ('COM-0008', 'Aguas y Luz del Valle', 'Servicios básicos'),
    ('COM-0009', 'Minimarket La Esquina', 'Supermercado'),
    ('COM-0010', 'Sushi Pacífico', 'Restaurantes'),
    ('COM-0011', 'Farmacia Del Sur', 'Farmacia'),
    ('COM-0042', 'Electro Sur', 'Tecnología')
ON CONFLICT (codigo) DO NOTHING;
