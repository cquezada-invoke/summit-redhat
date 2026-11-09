#!/bin/bash
# Arranque de la VM con GPU de la Fábrica (Ubuntu 22.04 + NVIDIA L4): driver, Docker con soporte de GPU y vLLM con un modelo
# abierto de código (API compatible con OpenAI en el puerto 8000, solo red interna). Idempotente: se puede correr en cada arranque.
set -x
MODELO=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/attributes/modelo || true)
MODELO=${MODELO:-Qwen/Qwen2.5-Coder-14B-Instruct-AWQ}
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y ubuntu-drivers-common && ubuntu-drivers install --gpgpu nvidia:550-server && apt-get install -y nvidia-utils-550-server
  reboot; exit 0
fi
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
