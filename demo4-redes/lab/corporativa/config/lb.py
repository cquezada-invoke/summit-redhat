# Balanceador de la DMZ (TCP, round robin): python3 lb.py <puerto> <backend:puerto>[,<backend:puerto>…]
import itertools, socket, sys, threading
puerto, backends = int(sys.argv[1]), [(b.split(":")[0], int(b.split(":")[1])) for b in sys.argv[2].split(",")]
rr = itertools.cycle(backends)
def tubo(a, b):
    try:
        while (d := a.recv(65536)):
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try: s.close()
            except OSError: pass
def atender(c):
    for _ in range(len(backends)):
        try:
            s = socket.create_connection(next(rr), timeout=3); break
        except OSError:
            s = None
    if not s:
        c.close(); return
    threading.Thread(target=tubo, args=(c, s), daemon=True).start(); tubo(s, c)
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("0.0.0.0", puerto)); srv.listen(64)
while True:
    c, _ = srv.accept(); threading.Thread(target=atender, args=(c,), daemon=True).start()
