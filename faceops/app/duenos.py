"""Dueño de cada componente (máquina con su sistema operativo, plataforma, equipo de red, storage…): la persona responsable,
a quien FaceOps envía las recomendaciones (buenas prácticas no cumplidas, hallazgos). Se asigna desde la ficha del componente."""
from .bd import ahora, auditar, ej, q, q1


def _tabla():
    from .bd import con
    con().executescript("""CREATE TABLE IF NOT EXISTS duenos (
      id INTEGER PRIMARY KEY, cliente_id INTEGER NOT NULL, tipo TEXT NOT NULL, clave TEXT NOT NULL,
      usuario TEXT DEFAULT '', nombre TEXT NOT NULL, correo TEXT DEFAULT '', asignado_por TEXT, asignado TEXT,
      UNIQUE(cliente_id, tipo, clave));""")


def de(cliente_id, tipo, clave):
    _tabla()
    return q1("SELECT * FROM duenos WHERE cliente_id=? AND tipo=? AND clave=?", cliente_id, tipo, str(clave))


def candidatos(cliente_id):
    """Usuarios con perfil en el cliente (se puede elegir uno o escribir otra persona)."""
    return q("SELECT u.usuario, u.nombre, u.correo FROM perfiles p JOIN usuarios u ON u.id=p.usuario_id "
             "WHERE p.cliente_id=? AND u.activo=1 ORDER BY u.nombre", cliente_id)


def asignar(cliente_id, tipo, clave, f, quien):
    _tabla()
    usuario = (f.get("dueno_usuario") or "").strip()
    nombre, correo = (f.get("dueno_nombre") or "").strip()[:80], (f.get("dueno_correo") or "").strip()[:120]
    if usuario:
        u = next((x for x in candidatos(cliente_id) if x["usuario"] == usuario), None)
        if not u:
            raise ValueError("Elige un usuario del cliente")
        nombre, correo = u["nombre"], u["correo"] or correo
    if not nombre:
        ej("DELETE FROM duenos WHERE cliente_id=? AND tipo=? AND clave=?", cliente_id, tipo, str(clave))
        auditar(quien, cliente_id, "quitar dueño", f"{tipo}:{clave}")
        return None
    if correo and ("@" not in correo or " " in correo):
        raise ValueError("Correo no válido")
    ej("INSERT INTO duenos(cliente_id, tipo, clave, usuario, nombre, correo, asignado_por, asignado) VALUES (?,?,?,?,?,?,?,?) "
       "ON CONFLICT(cliente_id, tipo, clave) DO UPDATE SET usuario=excluded.usuario, nombre=excluded.nombre, correo=excluded.correo, "
       "asignado_por=excluded.asignado_por, asignado=excluded.asignado", cliente_id, tipo, str(clave), usuario, nombre, correo, quien, ahora())
    auditar(quien, cliente_id, "asignar dueño", f"{tipo}:{clave}", {"dueno": nombre})
    return de(cliente_id, tipo, clave)
