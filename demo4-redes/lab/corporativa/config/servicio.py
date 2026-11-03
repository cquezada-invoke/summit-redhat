# Servicio de prueba de una zona: responde por TCP en el puerto indicado (HTTP si es 8080/80) con su nombre y zona
import socket, sys, threading
puerto, nombre = int(sys.argv[1]), sys.argv[2]
def atender(c):
    try:
        c.settimeout(3)
        try: c.recv(4096)
        except OSError: pass
        cuerpo = f"{nombre} responde OK\n".encode()
        c.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: " + str(len(cuerpo)).encode() + b"\r\n\r\n" + cuerpo)
    finally:
        c.close()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", puerto)); s.listen(32)
while True:
    c, _ = s.accept(); threading.Thread(target=atender, args=(c,), daemon=True).start()
