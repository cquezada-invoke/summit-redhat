# =====================================================================================================================
#  FaceOps · paquete de instalación para {{ c.nombre }}  (Windows Server con Podman · EN VALIDACIÓN)
#  Instancia: {{ c.instancia_id }}      Versión fijada: {{ commit[:12] }}      Generado: {{ generado }}
#  Este archivo contiene la LLAVE PROPIA de la instancia: no lo publiques ni lo reutilices en otro cliente.
#  Requisitos: Podman para Windows (podman machine iniciada) y Git. Ejecutar en PowerShell como administrador.
# =====================================================================================================================
$ErrorActionPreference = "Stop"
$Instancia = "{{ c.instancia_id }}"; $Codigo = "{{ c.codigo }}"; $Nombre = "{{ c.nombre }}"
$Repo = "{{ repo }}"; $Commit = "{{ commit }}"; $Admin = "{{ admin }}"; $Llave = "{{ tok }}"
foreach ($c in "podman", "git") { if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { throw "Falta $c: instálalo (winget install RedHat.Podman / Git.Git)" } }
$AapUrl = if ($env:AAP_URL) { $env:AAP_URL } else { Read-Host "URL de Ansible Automation Platform (https://...)" }
$AapToken = if ($env:AAP_TOKEN) { $env:AAP_TOKEN } else { [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host "Token de AAP para FaceOps" -AsSecureString))) }
$Base = "C:\ProgramData\FaceOps"; $R = "$Base\repo"
New-Item -ItemType Directory -Force -Path $Base, "$Base\datos" | Out-Null
if (-not (Test-Path "$R\.git")) { git clone -q $Repo $R }
git -C $R fetch -q origin; git -C $R checkout -q $Commit
$ver = @{ linea = "1.0"; build = [int](git -C $R rev-list --count HEAD); commit = $Commit; fecha = (git -C $R log -1 --format=%cd --date=format:'%Y-%m-%d %H:%M'); rama = "main" }
$ver | ConvertTo-Json -Compress | Set-Content -Encoding UTF8 "$R\faceops\app\version.json"
podman build -q -t localhost/faceops:latest -f "$R\faceops\Containerfile" "$R\faceops" | Out-Null
@"
FACEOPS_INSTANCIA_ID=$Instancia
FACEOPS_CLIENTE_CODIGO=$Codigo
FACEOPS_CLIENTE_NOMBRE=$Nombre
FACEOPS_INSTANCIA=$Codigo
FACEOPS_ROL=cliente
FACEOPS_AAP_URL=$AapUrl
FACEOPS_AAP_TOKEN=$AapToken
FACEOPS_ADMIN_URL=$Admin
FACEOPS_ADMIN_TOKEN=$Llave
FACEOPS_CONSULTOR_USUARIO={{ c.consultor_usuario or '' }}
FACEOPS_CONSULTOR_NOMBRE={{ c.consultor_nombre or '' }}
FACEOPS_CONSULTOR_CORREO={{ c.consultor_correo or '' }}
SCRIPT_NAME=/faceops
"@ | Set-Content -Encoding ASCII "$Base\faceops.env"
icacls "$Base\faceops.env" /inheritance:r /grant:r "Administrators:F" | Out-Null
podman rm -f faceops 2>$null | Out-Null
podman run -d --name faceops --restart always -p 8843:8110 -v faceops-datos:/var/lib/faceops --env-file "$Base\faceops.env" -e TZ=America/Santiago localhost/faceops:latest | Out-Null
New-NetFirewallRule -DisplayName "FaceOps 8843" -Direction Inbound -Protocol TCP -LocalPort 8843 -Action Allow -ErrorAction SilentlyContinue | Out-Null
$Ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" } | Select-Object -First 1).IPAddress
$Url = "http://$($Ip):8843/faceops"
try { Invoke-RestMethod -Method Post -Uri "$Admin/api/instancia/registro" -Headers @{ "X-FaceAdmin-Token" = $Llave } -ContentType "application/json" `
      -Body (@{ instancia_id = $Instancia; url = $Url; host = $env:COMPUTERNAME; commit = $Commit } | ConvertTo-Json) -SkipCertificateCheck | Out-Null } catch { }
Write-Host "FaceOps listo: $Url/  (instancia $Instancia)"
