# Ollama LLM container

Runs the local LLM that Orpheus uses for transcript cleanup, with all models
stored on `E:\A_I`.

## First run

```powershell
# Ensure the model directory exists
New-Item -ItemType Directory -Force E:\A_I | Out-Null

docker compose -f deploy/ollama/docker-compose.yml up -d
```

The `model-loader` sidecar pulls `llama3.1:8b` on first start (into
`E:\A_I\models`) and then exits. Change the model in both the compose file and
Orpheus **Settings > Ollama model** if you want a different one.

Verify:

```powershell
docker exec ollama ollama list
curl http://localhost:11434/api/tags
```

Point Orpheus at it — this is already the default: **Settings > Ollama URL** =
`http://localhost:11434`.

## Pinned version

The image is pinned to `ollama/ollama:0.6.8`. To move to the current release:

```powershell
docker pull ollama/ollama:latest
docker exec ollama ollama --version   # read the version, e.g. 0.7.x
```

Then edit the `image:` tag in `docker-compose.yml` to that version and
`docker compose ... up -d` again. Models on `E:\A_I` survive the upgrade.

## GPU

On **Windows Docker Desktop the RX 6800 XT is not available to the container**
— ROCm passthrough to the WSL2 backend isn't supported for consumer Radeon, so
the container is CPU-only. If you want the LLM on the GPU, run Ollama natively
on Windows (its installer bundles ROCm and supports this card); the compose
file's rocm block is only for a future Linux host.

## Autostart

Docker Desktop set to "Start when you log in" (Settings > General) plus
`restart: unless-stopped` brings this container up automatically at boot.
