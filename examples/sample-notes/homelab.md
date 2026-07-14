# Homelab journal

The backup situation is finally sane: nightly restic snapshots to the NAS,
weekly sync to B2. The part I keep re-learning is that untested backups are
not backups — the March restore drill found a repo password that only existed
in my shell history.

Moved the reverse proxy from nginx configs to Caddy. Automatic certificates
remove a whole category of Sunday-night maintenance, and the Caddyfile is
short enough to actually read. The old nginx setup had grown 400 lines of
copy-pasted location blocks.

Power draw audit: the idle server pulls 60W, which is 40 euros a quarter.
Swapped the spinning disks' spindown timers and moved two always-on services
to the fanless mini PC. Down to 38W idle. Electricity cost is a real design
constraint for anything running 24/7 at home.

The monitoring stack was more useful this month than the services it watches.
Uptime Kuma caught the DNS resolver flapping before I noticed; the Grafana
disk-usage panel predicted the NAS filling three weeks out. Observability
pays for its own maintenance time.

Tailscale keeps winning. Every time I consider opening a port to the
internet, I remember the tailnet already solves it with zero exposed surface.
The only public thing left is the static blog, and that's on a CDN anyway.

DNS lesson again: split-horizon DNS is the root of half my weird issues.
Laptop on VPN resolves the NAS to the tailnet IP, phone on WiFi resolves it
to the LAN IP, and every debugging session starts with "which network am I
actually on?" Should write the runbook page for this instead of re-deriving
it.

Docker compose files are drifting between hosts again. The fix is the same
as last time: one git repo, one directory per host, deploy by pull. I keep
not doing it because it feels like overkill for six containers, and I keep
paying for not doing it.

Quarterly restore drill is now a calendar event. Restic restore to a scratch
directory, checksum a sample, delete. Twenty minutes that turns "I think we
have backups" into "we have backups."
