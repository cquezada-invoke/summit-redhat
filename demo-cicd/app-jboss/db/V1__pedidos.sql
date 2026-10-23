-- Estructura inicial de la app: tabla de pedidos con datos de ejemplo.
CREATE TABLE pedidos (
    id        serial PRIMARY KEY,
    cliente   text NOT NULL,
    monto     numeric(12, 2) NOT NULL,
    creado    timestamptz NOT NULL DEFAULT now()
);

INSERT INTO pedidos (cliente, monto) VALUES
    ('Banco Andino', 125000),
    ('Retail Sur', 48900),
    ('Seguros del Pacífico', 310500);
