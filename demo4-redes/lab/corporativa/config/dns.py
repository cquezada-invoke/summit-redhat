# DNS autoritativo mínimo de la DMZ (solo registros A de la zona corp.invoke.lab)
import socket, struct
ZONA = {"www.corp.invoke.lab": "198.51.100.10", "app.corp.invoke.lab": "10.20.0.10", "bd.corp.invoke.lab": "10.30.0.10",
        "nas.corp.invoke.lab": "10.50.0.10", "lb.corp.invoke.lab": "10.10.10.11", "proxy.corp.invoke.lab": "10.10.10.12"}
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", 53))
while True:
    q, a = s.recvfrom(512)
    i, partes = 12, []
    while q[i]:
        partes.append(q[i + 1:i + 1 + q[i]].decode(errors="replace")); i += q[i] + 1
    nombre, fin = ".".join(partes).lower(), i + 5
    ip = ZONA.get(nombre)
    cab = q[:2] + (b"\x84\x00" if ip else b"\x84\x03") + q[4:6] + (b"\x00\x01" if ip else b"\x00\x00") + b"\x00\x00\x00\x00"
    r = cab + q[12:fin]
    if ip:
        r += b"\xc0\x0c\x00\x01\x00\x01" + struct.pack(">IH", 300, 4) + socket.inet_aton(ip)
    s.sendto(r, a)
