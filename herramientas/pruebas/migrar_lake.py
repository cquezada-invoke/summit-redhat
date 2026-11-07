import sys, requests, xml.etree.ElementTree as ET
VIEJO, NUEVO = sys.argv[1], sys.argv[2]
ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
requests.put(f"{NUEVO}/lake", timeout=30)
def listar(base):
    out, tok = [], None
    while True:
        p = {"list-type": "2", "max-keys": "1000"}
        if tok: p["continuation-token"] = tok
        x = ET.fromstring(requests.get(f"{base}/lake", params=p, timeout=60).content)
        out += [(c.find("s:Key", ns).text, int(c.find("s:Size", ns).text)) for c in x.findall("s:Contents", ns)]
        t = x.find("s:NextContinuationToken", ns)
        if t is None or not t.text: return out
        tok = t.text
v = listar(VIEJO); ya = dict(listar(NUEVO)); n = b = 0
for k, sz in v:
    if ya.get(k) == sz: continue
    r = requests.get(f"{VIEJO}/lake/{k}", timeout=120); r.raise_for_status()
    requests.put(f"{NUEVO}/lake/{k}", data=r.content, headers={"Content-Type": r.headers.get("Content-Type", "application/octet-stream")}, timeout=120).raise_for_status()
    n += 1; b += len(r.content)
nv = listar(NUEVO)
print(f"objetos origen {len(v)} · copiados {n} ({b/1e6:.1f} MB) · destino {len(nv)} · bytes origen {sum(s for _, s in v)/1e6:.1f} MB destino {sum(s for _, s in nv)/1e6:.1f} MB")
