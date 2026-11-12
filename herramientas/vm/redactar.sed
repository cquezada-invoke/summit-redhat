# Redacción de secretos en la salida de las tareas de la VM antes de publicarla en vm-resultados
# (rama pública). Uso: sed -E -i -f herramientas/vm/redactar.sed <archivo>
# Campos JSON cuyo nombre indica credencial (faceops_autorizacion, token, password, secret...)
s/("[A-Za-z0-9_-]*(autorizacion|authorization|token|secret|password|passwd|api_key|apikey)[A-Za-z0-9_-]*"[[:space:]]*:[[:space:]]*")[^"]*/\1<REDACTADO>/Ig
# Cabeceras Authorization (Bearer / Basic / Token) en cualquier formato
s/(authorization["']?[[:space:]]*[:=][[:space:]]*["']?)(bearer[[:space:]]+|basic[[:space:]]+|token[[:space:]]+)?[^"'[:space:],}]+/\1<REDACTADO>/Ig
s/(bearer[[:space:]]+)[A-Za-z0-9._~+/=-]{10,}/\1<REDACTADO>/Ig
# JWT (header.payload[.firma]) completos o truncados
s/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*(\.[A-Za-z0-9_-]*)?/<REDACTADO-JWT>/g
s/(^|[^A-Za-z0-9+\/_-])eyJ[A-Za-z0-9_-]{16,}/\1<REDACTADO-JWT>/g
# Asignaciones tipo VAR=valor / clave: valor (ini, env, yaml)
s/^([[:space:]]*[A-Za-z0-9_]*(TOKEN|SECRET|PASSWD|PASSWORD)[A-Za-z0-9_]*[[:space:]]*[:=][[:space:]]*)[^[:space:]].*/\1<REDACTADO>/I
s/((token|password|passwd|secret)[[:space:]]*[:=][[:space:]]*)[^[:space:]"',}]{8,}/\1<REDACTADO>/Ig
# Credenciales embebidas en URLs
s/(x-access-token:)[^@[:space:]]+@/\1<REDACTADO>@/g
s/(https?:\/\/[^:\/@[:space:]]+:)[^@\/[:space:]]+@/\1<REDACTADO>@/g
