#!/bin/bash
# Twin host setup (Amazon Linux 2023), run once by EC2 user data after the code is unpacked to /opt/trusted-rapp.
# Python 3.12 + CPU torch via uv, then the framework as a systemd service on :8700 (twin on 127.0.0.1:8701).
set -euxo pipefail
APP=/opt/trusted-rapp
export UV_INSTALL_DIR=/usr/local/bin UV_PYTHON_INSTALL_DIR=/opt/uv-python HOME=/root
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.12
uv venv --python 3.12 "$APP/.venv"
PY="$APP/.venv/bin/python"
uv pip install --python "$PY" --index-url https://download.pytorch.org/whl/cpu torch==2.4.1
uv pip install --python "$PY" -r "$APP/requirements.txt" boto3
install -m 0755 "$APP/infra/host/start.sh" /usr/local/bin/trusted-rapp-start
cat > /etc/systemd/system/trusted-rapp.service <<'UNIT'
[Unit]
Description=Trusted rApp Framework (MAVERIC twin + SMO enforcement point + demo page)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/trusted-rapp
EnvironmentFile=/etc/trusted-rapp.env
ExecStart=/usr/local/bin/trusted-rapp-start
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now trusted-rapp.service
