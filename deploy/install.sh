#!/usr/bin/env bash
# One-time setup inside the yt-podcast container (Debian/Ubuntu). Run as root from /opt/yt-podcast.
set -euo pipefail
cd "$(dirname "$0")/.."

apt-get update
apt-get install -y python3 python3-venv ffmpeg curl unzip ca-certificates

# yt-dlp needs a JavaScript runtime for YouTube; Deno is the recommended one.
if ! command -v deno >/dev/null; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s -- -y
fi

id ytpodcast >/dev/null 2>&1 || useradd --system --home /opt/yt-podcast --shell /usr/sbin/nologin ytpodcast

python3 -m venv venv
venv/bin/pip install --quiet --upgrade pip "yt-dlp[default]"

mkdir -p /srv/yt-podcast/public /srv/yt-podcast/state
chown -R ytpodcast:ytpodcast /srv/yt-podcast venv

[ -f config.toml ] || cp config.example.toml config.toml
chown ytpodcast:ytpodcast config.toml

cp deploy/yt-podcast.service deploy/yt-podcast.timer /etc/systemd/system/
systemctl daemon-reload
echo
echo "Installed. Next: edit /opt/yt-podcast/config.toml, then: systemctl enable --now yt-podcast.timer"
