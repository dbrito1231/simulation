# GitServ simulation — single Flask container (host-native Ollama via SIM_OLLAMA_HOST).
FROM python:3.12-slim

# uv 0.9.5 — matches host toolchain; pin tag for reproducible builds.
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

COPY simulation ./simulation

EXPOSE 5001

# Run (foreground, titled cmd window — closing the window stops the sim; no -d, no --restart):
#
# Build: docker build -t gitserv-sim .
# Pre-create bind targets (empty state.db file, memory_store.json as {}, logs/ dir).
# Omit state.db-wal / state.db-shm -v mounts unless those paths exist as files on the host.
#
# Before starting, confirm ≤1 server instance (container and native):
#   docker ps -a --filter name=gitserv-sim
#   Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation.server' }
# Stop an existing container: docker stop gitserv-sim && docker rm gitserv-sim
#
# Start-Process cmd.exe -ArgumentList '/k', 'title simserver && docker run --name gitserv-sim -p 5001:5001 -e SIM_OLLAMA_HOST=host.docker.internal:11434 -v "%CD%\simulation\state.db:/app/simulation/state.db" -v "%CD%\simulation\logs:/app/simulation/logs" -v "%CD%\simulation\memory_store.json:/app/simulation/memory_store.json" gitserv-sim' -WorkingDirectory $PWD
#
# Optional pass-through (defaults in server.py): -e SIM_HOST=0.0.0.0 -e SIM_PORT=5001 -e SIM_AGENTS=12

CMD ["uv", "run", "python", "simulation/server.py"]
