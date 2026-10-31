// Front del Catálogo en línea: muestra los productos que entrega el servicio REST (JBoss). Sin dependencias externas.
const http = require("http");
const WS = process.env.WS_URL || "http://ws:8080";
const PUERTO = process.env.PORT || 8080;
const VERSION = process.env.APP_VERSION || "1.0";

const pagina = (cuerpo) => `<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catálogo en línea</title><style>body{font-family:system-ui,Arial,sans-serif;margin:0;background:#f5f5f5;color:#151515}
header{background:#151515;color:#fff;padding:16px 24px;border-bottom:4px solid #ee0000}main{max-width:900px;margin:24px auto;padding:0 16px}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}.p{background:#fff;border:1px solid #ddd;border-radius:8px;padding:14px}
.p b{display:block}.precio{color:#0066cc;font-size:20px;margin-top:6px}.pie{color:#6a6e73;font-size:12px;margin-top:20px}</style></head>
<body><header><b>Catálogo en línea</b> · Invoke</header><main>${cuerpo}</main></body></html>`;

http.createServer(async (req, res) => {
  if (req.url === "/salud") { res.writeHead(200, {"Content-Type": "text/plain"}); return res.end("ok"); }
  try {
    const r = await fetch(`${WS}/api/productos`, {signal: AbortSignal.timeout(5000)});
    const d = await r.json();
    const items = d.productos.map(p => `<div class="p"><b>${p.nombre}</b><span>${p.categoria}</span><div class="precio">$ ${p.precio.toLocaleString("es-CL")}</div></div>`).join("");
    res.writeHead(200, {"Content-Type": "text/html; charset=utf-8"});
    res.end(pagina(`<h1>Productos</h1><div class="g">${items}</div>
      <p class="pie">Servicio: ${d.servidor} · Base de datos: ${d.base} · front v${VERSION} en ${require("os").hostname()}</p>`));
  } catch (e) {
    res.writeHead(503, {"Content-Type": "text/html; charset=utf-8"});
    res.end(pagina(`<h1>Productos</h1><p>El servicio de productos no responde (${e.message}).</p>`));
  }
}).listen(PUERTO, () => console.log(`catalogo-web en :${PUERTO} → ${WS}`));
