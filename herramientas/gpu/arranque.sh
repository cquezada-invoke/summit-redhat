#!/bin/bash
# Arranque de la VM con GPU de la Fábrica (Ubuntu 22.04 + NVIDIA L4): driver, Docker con soporte de GPU y vLLM con un modelo
# abierto de código (API compatible con OpenAI en el puerto 8000, solo red interna). Idempotente: se puede correr en cada arranque.
set -x
MODELO=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/modelo || true)
MODELO=${MODELO:-Qwen/Qwen2.5-Coder-14B-Instruct-AWQ}
export DEBIAN_FRONTEND=noninteractive
M=/var/lib/fabrica-gpu-reinicio   # marca: se reinicia una sola vez por instalación del driver (evita reinicios en bucle)
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
  if [ -f $M ]; then
    echo "El driver de NVIDIA no cargó después de reiniciar: no se reinicia de nuevo (revisar a mano)"; exit 1
  fi
  apt-get update -y
  # módulos firmados ya compilados para el kernel de GCP; si no existen, el driver con DKMS
  apt-get install -y "linux-modules-nvidia-550-server-$(uname -r)" nvidia-utils-550-server \
    || apt-get install -y "linux-headers-$(uname -r)" nvidia-driver-550-server nvidia-utils-550-server
  if modprobe nvidia 2>/dev/null && nvidia-smi >/dev/null 2>&1; then
    echo "driver cargado sin reiniciar"
  else
    touch $M; reboot; exit 0
  fi
fi
rm -f $M
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -y && apt-get install -y nvidia-container-toolkit && nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
fi
docker rm -f vllm 2>/dev/null
docker run -d --name vllm --restart unless-stopped --gpus all -p 8000:8000 -v /opt/modelos:/root/.cache/huggingface --ipc=host \
  vllm/vllm-openai:v0.6.6.post1 --model "$MODELO" --served-model-name "$MODELO" --max-model-len 24576 --gpu-memory-utilization 0.92 \
  --enable-auto-tool-choice --tool-call-parser hermes
