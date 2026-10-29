# yt-podcast

Turns YouTube playlists into a private audio podcast feed you can add to any podcast app
(Shuttle: Library → Import & Export → Add RSS Feed).

- Downloads the original audio (m4a) of every video in the configured playlists
- Cuts sponsor segments with SponsorBlock
- One show per feed, with seasons (e.g. one season per campaign) and episode numbers
- Runs every 3 hours via a systemd timer; yt-dlp updates itself before each run

## Commands

```
venv/bin/python ytpodcast.py list   # show planned episodes + season/episode numbers, no downloads
venv/bin/python ytpodcast.py run    # download new episodes and rebuild the feed
venv/bin/python ytpodcast.py feed   # rebuild the feed XML only
```

## Setup (in the container, as root)

```
git clone https://github.com/jdatje/yt-podcast /opt/yt-podcast
/opt/yt-podcast/deploy/install.sh
nano /opt/yt-podcast/config.toml
systemctl enable --now yt-podcast.timer
```

Served publicly with Tailscale Funnel under a secret path:

```
tailscale funnel --bg --set-path /<token> /srv/yt-podcast/public
```

Feed URL: `https://<host>.<tailnet>.ts.net/<token>/<slug>.xml`

## Update

```
cd /opt/yt-podcast && git pull && systemctl start yt-podcast.service
```

Logs: `journalctl -u yt-podcast -n 50`
