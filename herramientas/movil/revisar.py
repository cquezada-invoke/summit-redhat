"""Revisa FaceOps en pantalla de teléfono (390 px): por página, el ancho total y los elementos que se salen del borde.
Uso (en la VM): python3 revisar.py <cookie de sesión> [rutas...]"""
import sys
from playwright.sync_api import sync_playwright

BASE = "https://faceops.34.176.42.56.sslip.io:8843/faceops"
RUTAS = sys.argv[2:] or ["/", "/inventario", "/inventario?forma=fisico", "/inventario?forma=fisico&marca=dell", "/inventario/21", "/inventario/86",
                         "/tickets", "/deudas", "/monitoreo/radar", "/alertas", "/gobierno-ti", "/seguridad/mapa", "/configuracion/usuarios",
                         "/automatizaciones", "/plataformas", "/segmentos", "/monitoreo/kpi-okr", "/monitoreo/itil"]
JS = """() => { const W = innerWidth, out = [];
  document.querySelectorAll('body *').forEach(el => { const r = el.getBoundingClientRect(); if (!r.width || getComputedStyle(el).visibility === 'hidden') return;
    if (r.right > W + 2 || r.left < -2) { let p = el.parentElement, dentro = false;
      while (p && p !== document.body) { const cs = getComputedStyle(p); if (/(auto|scroll|hidden)/.test(cs.overflowX)) { dentro = true; break; } p = p.parentElement; }
      if (!dentro) out.push((el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '')) + ' [' + Math.round(r.left) + '→' + Math.round(r.right) + ']'); } });
  const raiz = out.filter((x, i) => i < 400); return {ancho: document.documentElement.scrollWidth, alto: document.documentElement.scrollHeight, fuera: raiz}; }"""
with sync_playwright() as p:
    b = p.chromium.launch(args=["--ignore-certificate-errors"])
    c = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, ignore_https_errors=True, is_mobile=True, has_touch=True)
    c.add_cookies([{"name": "session", "value": sys.argv[1], "url": BASE + "/"}])
    pg = c.new_page()
    for r in RUTAS:
        try:
            resp = pg.goto(BASE + r, wait_until="networkidle", timeout=45000)
            pg.wait_for_timeout(800)
            x = pg.evaluate(JS)
            vistos, sal = set(), []
            for f in x["fuera"]:
                k = f.split(" [")[0]
                if k not in vistos:
                    vistos.add(k); sal.append(f)
            print(f"== {r} · HTTP {resp.status} · ancho {x['ancho']} · alto {x['alto']} · {len(x['fuera'])} fuera")
            for f in sal[:12]:
                print("   ", f)
            pg.screenshot(path=f"/tmp/movil{r.replace('/', '_').replace('?', '_').replace('&', '_').replace('=', '-')}.png", full_page=False)
        except Exception as e:  # noqa: BLE001
            print(f"== {r} · ERROR {e}")
    b.close()
