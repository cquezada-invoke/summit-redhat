"""Consola de Redis dentro de FaceOps: entra con la credencial guardada en la bóveda (faceops/web/<equipo>), sin mostrarla.
Lectura para todos los perfiles; los comandos que escriben solo para perfiles de escritura. Todo queda auditado."""
import redis

LECTURA = {"GET", "MGET", "STRLEN", "EXISTS", "TYPE", "TTL", "PTTL", "HGET", "HGETALL", "HKEYS", "HLEN", "LRANGE", "LLEN",
           "SMEMBERS", "SCARD", "ZRANGE", "ZCARD", "SCAN", "DBSIZE", "INFO", "PING", "MEMORY", "OBJECT", "XLEN", "XRANGE"}
ESCRITURA = {"SET", "DEL", "EXPIRE", "PERSIST", "HSET", "HDEL", "LPUSH", "RPUSH", "LPOP", "RPOP", "SADD", "SREM", "ZADD", "ZREM",
             "INCR", "DECR", "INCRBY", "RENAME", "UNLINK"}


def conectar(e):
    from .boveda import web
    c = web(e["nombre"])
    if not c or c.get("sitio") != "Redis":
        raise LookupError("No hay credencial de Redis en la bóveda para este equipo (vuelve a ejecutar «Habilitar Redis»)")
    return redis.Redis(host=c.get("host") or e["ip"], port=int(c.get("puerto") or 6379), username=None, password=c["clave"],
                       socket_timeout=5, socket_connect_timeout=5, decode_responses=True)


def resumen(r):
    i = r.info()
    hits, misses = i.get("keyspace_hits", 0), i.get("keyspace_misses", 0)
    return {"version": i.get("redis_version"), "modo": i.get("redis_mode"), "uptime_h": round(i.get("uptime_in_seconds", 0) / 3600, 1),
            "clientes": i.get("connected_clients"), "memoria": i.get("used_memory_human"), "maxima": i.get("maxmemory_human"),
            "politica": i.get("maxmemory_policy"), "aof": i.get("aof_enabled"), "rdb_ultimo": i.get("rdb_last_save_time"),
            "aciertos": round(100 * hits / (hits + misses)) if hits + misses else None, "ops": i.get("instantaneous_ops_per_sec"),
            "bases": {k: v for k, v in i.items() if k.startswith("db") and isinstance(v, dict)}}


def claves(r, patron="*", cursor=0, n=100):
    cursor, ks = r.scan(cursor=cursor, match=patron or "*", count=n)
    out = []
    pipe = r.pipeline()
    for k in ks:
        pipe.type(k)
        pipe.ttl(k)
    res = pipe.execute()
    for i, k in enumerate(ks):
        out.append({"clave": k, "tipo": res[2 * i], "ttl": res[2 * i + 1]})
    return cursor, sorted(out, key=lambda x: x["clave"])


def valor(r, k):
    t = r.type(k)
    v = {"string": lambda: r.get(k), "hash": lambda: r.hgetall(k), "list": lambda: r.lrange(k, 0, 99),
         "set": lambda: sorted(r.smembers(k))[:100], "zset": lambda: r.zrange(k, 0, 99, withscores=True),
         "stream": lambda: r.xrange(k, count=20)}.get(t, lambda: None)()
    return {"clave": k, "tipo": t, "ttl": r.ttl(k), "valor": v}


def comando(r, texto, puede_escribir):
    import shlex
    partes = shlex.split(texto or "")
    if not partes:
        raise ValueError("Escribe un comando")
    c = partes[0].upper()
    if c in ESCRITURA and not puede_escribir:
        raise PermissionError(f"{c} modifica datos: tu perfil es de lectura")
    if c not in LECTURA | ESCRITURA:
        raise PermissionError(f"{c} no está permitido desde FaceOps")
    if c == "MEMORY" and (len(partes) < 2 or partes[1].upper() != "USAGE"):
        raise PermissionError("Solo MEMORY USAGE")
    return r.execute_command(*partes), c in ESCRITURA
