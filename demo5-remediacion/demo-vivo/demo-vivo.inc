# Demo 5 en vivo: página y estado de solo lectura, sin login (servicio demo-vivo en 127.0.0.1:9471)
location = /demo-vivo { return 301 /demo-vivo/; }
location /demo-vivo/ {
    limit_except GET { deny all; }
    limit_req zone=general burst=60 nodelay;
    proxy_pass http://127.0.0.1:9471/;
}
