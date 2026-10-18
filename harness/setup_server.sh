#!/bin/bash
# Prepare an Ubuntu 22.04/24.04 server (run as root) for the multi-language test-generation tasks.
#   - Grading tasks only needs Docker:           bash harness/setup_server.sh docker
#   - Authoring/grading locally with tgm.py also needs the toolchains (Python 3.13 + pytest, Node 22, Go, Rust,
#     JDK 21, Ruby, PHP + Composer, gcc/g++):    bash harness/setup_server.sh all
# Toolchains are installed system-wide (not under /root) because tgm.py runs suites as the unprivileged `nobody` user.
set -euo pipefail
MODE=${1:-all}
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git docker.io
systemctl enable --now docker 2>/dev/null || service docker start || true
[ "$MODE" = docker ] && { docker run --rm hello-world > /dev/null && echo "docker OK"; exit 0; }

apt-get install -y --no-install-recommends build-essential python3 python3-venv unzip xz-utils \
  openjdk-21-jdk-headless maven ruby-full php-cli php-xml php-mbstring php-curl php-zip php-bcmath php-intl composer

# Python 3.13 with pytest 9.1.1 (the version the Ridges samples pin) at /opt/venv313-base (tgm.py's default TG_PYTHON)
if [ ! -x /opt/venv313-base/bin/python ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
  export UV_PYTHON_INSTALL_DIR=/opt/uv-python
  uv python install 3.13
  uv venv --python 3.13 /opt/venv313-base
  uv pip install --python /opt/venv313-base/bin/python pytest==9.1.1 iniconfig==2.3.0 packaging==26.3 pluggy==1.6.0 Pygments==2.21.0
  chmod -R a+rX /opt/uv-python /opt/venv313-base
fi

# Node.js 22
if ! node --version 2>/dev/null | grep -q '^v22'; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

# Go 1.24
if ! /usr/local/go/bin/go version 2>/dev/null | grep -q 'go1.24'; then
  rm -rf /usr/local/go
  curl -fsSL https://go.dev/dl/go1.24.7.linux-amd64.tar.gz | tar -C /usr/local -xz
  ln -sf /usr/local/go/bin/go /usr/local/go/bin/gofmt /usr/local/bin/
fi

# Rust (stable) in /opt so that `nobody` can use it; tgm.py passes RUSTUP_HOME through to the runner
export RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo
if [ ! -x /opt/cargo/bin/cargo ]; then
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable --no-modify-path
  chmod -R a+rX /opt/rustup /opt/cargo
  ln -sf /opt/cargo/bin/* /usr/local/bin/
fi
echo 'export RUSTUP_HOME=/opt/rustup' > /etc/profile.d/rustup.sh

mkdir -p /opt/tgm /opt/tgm-tools
chmod 755 /opt/tgm /opt/tgm-tools
echo "done. Open a new shell (or: export RUSTUP_HOME=/opt/rustup), then: bash harness/tgm_selftest.sh --docker"
