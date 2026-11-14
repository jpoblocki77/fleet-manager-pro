#!/usr/bin/env bash
# Fleet Manager licence server - one-step installer for a fresh Ubuntu 24.04 server (e.g. Kamatera).
#
#   sudo bash install.sh licence.YOURDOMAIN.com
#
# Run it from the uploaded "fleet-licence" folder (made by "Prepare Upload.bat" on your PC).
# It installs Python + Caddy + firewall, puts the licence server in /opt/fleet-licence as a service,
# sets up HTTPS for your address and a nightly backup. Safe to run again: it updates the program files
# but never overwrites the signing key, config or purchase records already on the server.
set -euo pipefail

DOMAIN="${1:-}"
APP=/opt/fleet-licence
SVC_USER=fmlicence
HERE="$(dirname "$(readlink -f "$0")")"

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Run it as root:  sudo bash install.sh licence.YOURDOMAIN.com"
if ! [[ "$DOMAIN" =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$ ]] || [[ "$DOMAIN" == *YOURDOMAIN* ]]; then
  fail "Give your licence address, e.g.:  sudo bash install.sh fleetmanager.servehttp.com"
fi
for f in license_server.py ed25519.py host_key.json server_config.json fleet-licence.service Caddyfile backup_licences.py; do
  [ -f "$HERE/$f" ] || fail "$f is missing from $HERE. Upload the whole fleet-licence folder made by Prepare Upload.bat."
done
if [ "$(cat "$HERE/fleet-licence.service" "$HERE/Caddyfile" "$HERE/backup_licences.py" | tr -dc '\r' | wc -c)" -gt 0 ]; then
  fail "These files have Windows line endings. Re-make the folder with Prepare Upload.bat and upload it again."
fi

say "Installing Python, Caddy and the firewall"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 caddy ufw curl
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || fail "Python 3.8 or newer is needed."

say "Creating the service user and $APP"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP"
install -m 644 "$HERE/license_server.py" "$HERE/ed25519.py" "$APP/"          # program: always updated
for f in host_key.json server_config.json licenses.db; do                     # data: never overwritten
  if [ -f "$APP/$f" ]; then
    echo "    kept the $f already on the server"
  elif [ -f "$HERE/$f" ]; then
    install -m 600 "$HERE/$f" "$APP/$f"
    echo "    installed $f"
  fi
done
if [ -f "$APP/licenses.db" ] && ! cmp -s "$HERE/host_key.json" "$APP/host_key.json"; then
  echo "    NOTE: the host_key.json you uploaded differs from the one on the server; the server's one was kept."
fi
chown -R "$SVC_USER:$SVC_USER" "$APP"
chmod 700 "$APP"

say "Installing the licence server service"
install -m 644 "$HERE/fleet-licence.service" /etc/systemd/system/fleet-licence.service
systemctl daemon-reload
systemctl enable fleet-licence >/dev/null
systemctl restart fleet-licence
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8750/v1/health >/dev/null 2>&1; then break; fi
  [ "$i" -eq 30 ] && { journalctl -u fleet-licence -n 30 --no-pager; fail "The licence server didn't start (log above)."; }
  sleep 1
done
echo "    running: $(curl -fsS http://127.0.0.1:8750/v1/health)"

say "Setting up HTTPS for $DOMAIN (Caddy)"
if [ -f /etc/caddy/Caddyfile ] && [ ! -f /etc/caddy/Caddyfile.before-fleet-licence ]; then
  cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-fleet-licence
fi
sed "s/licence\.YOURDOMAIN\.com/$DOMAIN/" "$HERE/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || fail "Caddy didn't accept the Caddyfile."
systemctl enable caddy >/dev/null
systemctl reload caddy 2>/dev/null || systemctl restart caddy

say "Nightly backup (03:00, kept 30 days, in /var/backups/fleet-licence)"
install -m 700 "$HERE/backup_licences.py" /usr/local/sbin/fleet-licence-backup.py
printf '# Fleet Manager licence database - nightly safe copy\n0 3 * * * root /usr/bin/python3 /usr/local/sbin/fleet-licence-backup.py >/dev/null 2>&1\n' \
  > /etc/cron.d/fleet-licence-backup
chmod 644 /etc/cron.d/fleet-licence-backup
[ -f "$APP/licenses.db" ] && /usr/bin/python3 /usr/local/sbin/fleet-licence-backup.py | sed 's/^/    /'

say "Firewall: only SSH, HTTP and HTTPS"
SSH_PORT="$(echo "${SSH_CONNECTION:-}" | awk '{print $4}')"
ufw allow OpenSSH >/dev/null
[ -n "$SSH_PORT" ] && ufw allow "$SSH_PORT/tcp" >/dev/null   # whatever port you're connected on now
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | sed 's/^/    /'

say "Checking https://$DOMAIN/v1/health"
ok=""
for i in $(seq 1 12); do
  if out="$(curl -fsS --max-time 10 "https://$DOMAIN/v1/health" 2>/dev/null)"; then ok=1; break; fi
  sleep 5
done
if [ -n "$ok" ]; then
  echo "    $out"
  echo
  echo "DONE. Your licence server is live at https://$DOMAIN"
else
  echo "    Not reachable over HTTPS yet. Usually the DNS A record for $DOMAIN isn't pointing at this server"
  echo "    yet, or hasn't spread. Caddy keeps trying; check again later in a browser:"
  echo "    https://$DOMAIN/v1/health      (logs: journalctl -u caddy -n 50)"
fi
echo
echo "Next, on your PC: License Manager -> 1) Set up -> address https://$DOMAIN"
echo "Then delete the uploaded folder from this server (it holds a copy of your signing key):"
echo "    rm -rf \"$HERE\""
