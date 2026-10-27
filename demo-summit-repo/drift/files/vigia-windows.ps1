# Vigía de la Demo 1 en Windows (nodo-windows). Corre como tarea programada de SYSTEM al iniciar el equipo.
# Cada 15 s compara los servicios críticos y los miembros del grupo Administradores con la última foto local;
# si algo cambió, avisa al panel de cumplimiento, que lanza AAP ("Drift · Windows") para corregir o registrar.
# Mientras Ansible corrige existe el archivo "corrigiendo" y el vigía no avisa (para no reportar la corrección).
param([string]$Panel = "http://192.168.122.1:8088", [string]$Servidor = "nodo-windows", [string[]]$Servicios = @("W3SVC", "W32Time"), [switch]$Foto, [switch]$Imprimir)
$dir = "C:\ProgramData\SummitDrift"
$ultimo = Join-Path $dir "ultimo.json"
$marca = Join-Path $dir "corrigiendo"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

function Estado {
    $s = [ordered]@{}
    foreach ($n in $Servicios) {
        $x = Get-Service -Name $n -ErrorAction SilentlyContinue
        if ($x) { $s[$n] = [ordered]@{ estado = $x.Status.ToString().ToLower(); inicio = $x.StartType.ToString().ToLower() } }
    }
    $a = @(Get-LocalGroupMember -SID "S-1-5-32-544" | ForEach-Object { $_.Name } | Sort-Object)
    return [ordered]@{ servicios = $s; administradores = $a }
}

function Quien([datetime]$desde) {
    # Quién hizo el cambio: auditoría de grupos (4732/4733) o el usuario del cambio de servicio (7040)
    try {
        $e = Get-WinEvent -FilterHashtable @{ LogName = "Security"; Id = 4732, 4733; StartTime = $desde } -MaxEvents 1 -ErrorAction Stop
        if ($e) { return $e.Properties[6].Value }
    } catch {}
    try {
        $e = Get-WinEvent -FilterHashtable @{ LogName = "System"; Id = 7040, 7036; StartTime = $desde } -MaxEvents 1 -ErrorAction Stop
        if ($e -and $e.UserId) { return ($e.UserId.Translate([System.Security.Principal.NTAccount]).Value -split "\\")[-1] }
    } catch {}
    return "desconocido"
}

if ($Imprimir) { (Estado) | ConvertTo-Json -Depth 4 -Compress; exit 0 }  # lo usa Ansible para leer el estado
if ($Foto) {  # lo usa Ansible después de corregir: deja la foto local al día y termina
    Set-Content -Path $ultimo -Value ((Estado) | ConvertTo-Json -Depth 4 -Compress) -Encoding UTF8
    Remove-Item $marca -Force -ErrorAction SilentlyContinue
    exit 0
}
$previoJson = $null; $previo = $null
if (Test-Path $ultimo) { $previoJson = (Get-Content $ultimo -Raw).Trim(); $previo = $previoJson | ConvertFrom-Json }
$revisado = Get-Date
while ($true) {
    try {
        if ((Test-Path $ultimo) -and -not (Test-Path $marca)) {
            # Ansible pudo dejar una foto nueva al corregir: se toma como referencia
            $f = (Get-Content $ultimo -Raw).Trim(); if ($f -ne $previoJson) { $previoJson = $f; $previo = $f | ConvertFrom-Json }
        }
        $actual = Estado
        $json = $actual | ConvertTo-Json -Depth 4 -Compress
        if (Test-Path $marca) {
            if (((Get-Date) - (Get-Item $marca).LastWriteTime).TotalMinutes -gt 5) { Remove-Item $marca -Force }
        } elseif ($null -eq $previoJson) {
            Set-Content -Path $ultimo -Value $json -Encoding UTF8; $previoJson = $json; $previo = $json | ConvertFrom-Json
        } elseif ($previoJson -ne $json) {
            $cambios = @()
            foreach ($n in $actual.servicios.Keys) {
                $p = $previo.servicios.$n; $c = $actual.servicios[$n]
                if ($p -and $p.estado -ne $c.estado) { $cambios += "Servicio ${n}: $($p.estado) → $($c.estado)" }
                if ($p -and $p.inicio -ne $c.inicio) { $cambios += "Servicio ${n} (inicio): $($p.inicio) → $($c.inicio)" }
            }
            $antes = @($previo.administradores)
            foreach ($m in $actual.administradores) { if ($antes -notcontains $m) { $cambios += "Administradores: + $m" } }
            foreach ($m in $antes) { if ($actual.administradores -notcontains $m) { $cambios += "Administradores: - $m" } }
            $ev = [ordered]@{
                tipo = "windows"; id = [guid]::NewGuid().ToString("N").Substring(0, 10); host = $Servidor
                usuario = (Quien $revisado.AddSeconds(-30)); como = "Windows"; origen_ip = ""; terminal = "Windows"
                comandos = @($cambios); archivos = @($cambios)
            }
            $body = [Text.Encoding]::UTF8.GetBytes(($ev | ConvertTo-Json -Depth 4))
            Invoke-RestMethod -Method Post -Uri "$Panel/api/eventos" -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 10 | Out-Null
            Set-Content -Path $ultimo -Value $json -Encoding UTF8; $previoJson = $json; $previo = $json | ConvertFrom-Json
        }
        $revisado = Get-Date
    } catch { }
    Start-Sleep -Seconds 15
}
