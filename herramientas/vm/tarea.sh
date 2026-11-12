sudo podman ps --format "{{.Names}} {{.Status}}" | head -30
podman ps --format "{{.Names}} {{.Status}}" 2>/dev/null | head -30
curl -sk -o /dev/null -w "gateway %{http_code}\n" https://localhost/api/controller/v2/ping/
uptime; free -g | head -2
