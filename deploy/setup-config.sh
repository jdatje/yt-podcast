#!/usr/bin/env bash
# Fills in config.toml (host name + secret token) and publishes the feed folder on Funnel port 8443.
# Safe to re-run: keeps an existing token.
set -euo pipefail
cd /opt/yt-podcast

host=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
if [ ! -f config.toml ] || grep -q 'HOSTNAME:8443/TOKEN\|CHANGE-ME' config.toml; then
  token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  sed -e "s#https://HOSTNAME:8443/TOKEN#https://$host:8443/$token#" config.example.toml > config.toml
fi
chown ytpodcast:ytpodcast config.toml
chmod 600 config.toml
base=$(python3 -c 'import tomllib; print(tomllib.load(open("config.toml","rb"))["base_url"])')
token=${base##*/}

# Extra path on the existing public port 8443; the app already on "/" keeps working.
tailscale funnel --bg --https=8443 --set-path "/$token" /srv/yt-podcast/public >/dev/null

echo
echo "Feed URL for Shuttle (Library > Import & Export > Add RSS Feed):"
echo
echo "  $base/dungeon-masters.xml"
echo
