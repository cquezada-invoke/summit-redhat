"""Identidad · Fuente de usuarios de FaceOps: lee una fuente (Active Directory, LDAP o el IGA midPoint) desde Ansible.

FaceOps nunca se conecta a la fuente: este script corre en el entorno de ejecución de AAP con la credencial de la fuente
(variables FO_ID_URL, FO_ID_BIND, FO_ID_CLAVE, FO_ID_TLS) y devuelve los usuarios con sus grupos o roles.
Uso: identidad_fuente.py <tipo ad|ldap|midpoint> <conf.json>
Salida JSON: {"usuarios": [{usuario, nombre, correo, grupos, dn}], "grupos_dn": {cn: dn}, "n_total"}"""
import json
import os
import ssl
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))   # ldap3 (Python puro) va con el proyecto


def _primero(v):
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return "" if v is None else str(v)


def ldap_leer(conf):
    from ldap3 import ALL_ATTRIBUTES, SUBTREE, Connection, Server, Tls  # noqa: F401
    url = os.environ["FO_ID_URL"]
    tls = Tls(validate=ssl.CERT_REQUIRED if os.environ.get("FO_ID_TLS", "true").lower() in ("1", "true", "yes", "si") else ssl.CERT_NONE)
    srv = Server(url, use_ssl=url.startswith("ldaps://"), tls=tls, connect_timeout=8)
    con = Connection(srv, user=os.environ["FO_ID_BIND"], password=os.environ["FO_ID_CLAVE"], receive_timeout=20, raise_exceptions=False)
    try:
        if conf.get("starttls") and not url.startswith("ldaps://"):
            con.open()
            con.start_tls()
        ok = con.bind()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"No se pudo conectar con la fuente ({url}): {e}")
    if not ok:
        raise SystemExit(f"La fuente rechazó la cuenta de servicio: {con.result.get('description')} {con.result.get('message', '')}".strip())
    at = [a for a in (conf.get("attr_usuario"), conf.get("attr_nombre"), conf.get("attr_correo"), conf.get("attr_grupos") or "memberOf") if a]
    gen = con.extend.standard.paged_search(conf.get("base_dn") or "", conf.get("filtro_lista") or "(objectClass=person)", search_scope=SUBTREE,
                                           attributes=at, paged_size=500, generator=True)
    out, gdn = [], {}
    for e in gen:
        if e.get("type") != "searchResEntry":
            continue
        a = e.get("attributes") or {}
        gr = a.get(conf.get("attr_grupos") or "memberOf") or []
        gr = gr if isinstance(gr, list) else [gr]
        for g in gr:
            gdn[str(g).split(",")[0].split("=", 1)[-1].strip().lower()] = str(g)
        us = _primero(a.get(conf.get("attr_usuario"))).strip().lower()
        if us:
            out.append({"usuario": us, "nombre": _primero(a.get(conf.get("attr_nombre"))) or us, "correo": _primero(a.get(conf.get("attr_correo"))),
                        "grupos": [str(g) for g in gr], "dn": e.get("dn", "")})
    con.unbind()
    return out, gdn


def midpoint_leer(conf):
    import requests
    url = os.environ["FO_ID_URL"].rstrip("/") + "/ws/rest/users?options=resolveNames"
    r = requests.get(url, auth=(os.environ["FO_ID_BIND"], os.environ["FO_ID_CLAVE"]), headers={"Accept": "application/json"}, timeout=30,
                     verify=os.environ.get("FO_ID_TLS", "true").lower() in ("1", "true", "yes", "si"))
    if r.status_code != 200:
        raise SystemExit(f"midPoint respondió {r.status_code}: {r.text[:200]}")
    d = r.json()
    o = d.get("object", d) if isinstance(d, dict) else d
    if isinstance(o, dict):
        o = o.get("object", o.get("user", [o]))
    out = []
    for u in (o if isinstance(o, list) else [o]):
        u = u.get("user", u)
        if (u.get("activation") or {}).get("effectiveStatus", "enabled") != "enabled":
            continue
        refs = u.get("roleMembershipRef") or []
        refs = refs if isinstance(refs, list) else [refs]
        org = u.get("parentOrgRef") or []
        org = org if isinstance(org, list) else [org]
        nom, us = u.get("fullName") or u.get("name"), u.get("name")
        nom = nom.get("orig") if isinstance(nom, dict) else nom
        us = us.get("orig") if isinstance(us, dict) else us
        if us:
            out.append({"usuario": str(us).strip().lower(), "nombre": str(nom or us), "correo": str(u.get("emailAddress") or ""),
                        "grupos": [str(x.get("targetName") or "") for x in refs + org if isinstance(x, dict) and x.get("targetName")],
                        "dn": f"midpoint:{u.get('oid', '')}"})
    return out, {}


def main():
    tipo, conf = sys.argv[1], json.load(open(sys.argv[2]))
    us, gdn = midpoint_leer(conf) if tipo == "midpoint" else ldap_leer(conf)
    print(json.dumps({"usuarios": us, "grupos_dn": gdn, "n_total": len(us)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
