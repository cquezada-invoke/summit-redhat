#!/bin/bash
grep -n -E "Traceback|Error|ERROR|Guardar credencial|falta" ~/faceops-autodeploy/configurar.log | grep -v -i -E "clave|password|token|secret" | cut -c1-220 | head -20; tail -5 ~/faceops-autodeploy/configurar.log | grep -v -i -E "clave|password|token|secret" | cut -c1-220
