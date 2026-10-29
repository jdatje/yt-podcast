#!/usr/bin/env bash
# (Re)builds config.toml from config.example.toml with this host's name and a secret token,
# and publishes the feed folder on Funnel port 8443. Re-run after pulling config changes:
# an existing token is kept, so the feed URL never changes.
set -euo pipefail
cd /opt/yt-podcast

host=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
token=""
if [ -f config.toml ]; then
  token=$(python3 -c 'import tomllib,re; b=tomllib.load(open("config.toml","rb"))["base_url"]; t=b.rsplit("/",1)[-1]; print(t if re.fullmatch(r"[0-9a-f]{24}", t) else "")' 2>/dev/null || true)
fi
[ -n "$token" ] || token=$(python3 -c 'import secrets; print(secrets.token_hex(12))')

sed -e "s#https://HOSTNAME:8443/TOKEN#https://$host:8443/$token#" config.example.toml > config.toml
chown ytpodcast:ytpodcast config.toml
chmod 600 config.toml

# Extra path on the existing public port 8443; the app already on "/" keeps working.
tailscale funnel --bg --https=8443 --set-path "/$token" /srv/yt-podcast/public >/dev/null

echo
echo "Feed URL for Shuttle (Library > Import & Export > Add RSS Feed):"
echo
echo "  https://$host:8443/$token/dungeon-masters.xml"
echo
