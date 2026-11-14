# Deploy the licence server to Kamatera (ready-made files)

This folder turns the manual steps in *Guides\Kamatera Hosting Guide.pdf* (Part 1, steps 3 to 9) into two steps.

| File | What it is | Where it ends up on the server |
|---|---|---|
| `Prepare Upload.bat` / `prepare_upload.py` | Run on **your PC**. Makes `upload\fleet-licence`, the exact files the server needs | (stays on your PC) |
| `install.sh` | Run once on the server. Does all the setup below | (run from the upload) |
| `fleet-licence.service` | Runs the licence server all the time, as its own locked-down user | `/etc/systemd/system/` |
| `Caddyfile` | HTTPS for your address, passing requests to the server | `/etc/caddy/Caddyfile` |
| `backup_licences.py` | Nightly safe copy of `licenses.db`, kept 30 days | `/usr/local/sbin/fleet-licence-backup.py` |

## Before you start

1. Create the Kamatera server with **Ubuntu Server 24.04**, the smallest size, and an SSH key login (guide, Part 1 step 1).
2. In your No-IP account, point **fleetmanager.servehttp.com** at the server's IP, and quit the No-IP update program on your home PC (guide, Part 1 step 2).
3. Copies you already sold use `http://fleetmanager.servehttp.com:8750`. This setup answers on HTTPS, so ask for the files to also answer on port 8750 before you move.

## 1. On your PC

Double-click **Prepare Upload.bat**. It makes `deploy\upload\fleet-licence` containing:
the server program, your signing key, admin token/config, a safe copy of your purchases (`licenses.db`) and the Linux files above.

## 2. On the server

Upload the whole `fleet-licence` folder to `/root/` with WinSCP, then connect (`ssh root@YOUR-IP`) and run:

```bash
sudo bash /root/fleet-licence/install.sh fleetmanager.servehttp.com
```

(your No-IP address; first point it at the Kamatera IP in your No-IP account). It:

- installs Python 3, Caddy, ufw and curl;
- creates the `fmlicence` user and `/opt/fleet-licence`; the key, config and database are readable only by that user;
- installs and starts the `fleet-licence` service on 127.0.0.1:8750 (not reachable from the internet directly);
- writes the Caddyfile for your address, so Caddy gets the HTTPS certificate by itself;
- sets up the nightly backup at 03:00 into `/var/backups/fleet-licence` and takes the first one straight away;
- turns on the firewall with only SSH, 80 and 443 open (it also keeps open the SSH port you're connected on);
- checks `https://YOUR-ADDRESS/v1/health` and tells you if it's live.

Running it again later is safe (for example, to update `license_server.py`). The program files are updated, but
the signing key, config and purchase records already on the server are **never overwritten**.

## 3. Finish

- On the server, delete the uploaded copy: `rm -rf /root/fleet-licence` (it contains your signing key).
- On your PC, delete `deploy\upload` for the same reason.
- On your PC, run **License Manager**, choose **1) Set up** and enter `https://fleetmanager.servehttp.com`. Then restart Fleet Manager
  (guide, Part 2).
- Stop running License Server.bat on your PC. The live records are on Kamatera now.

## Useful commands on the server

| What | Command |
|---|---|
| Is it running? | `systemctl status fleet-licence caddy` |
| Server log | `journalctl -u fleet-licence -n 50` |
| Restart | `systemctl restart fleet-licence` |
| Backup now | `python3 /usr/local/sbin/fleet-licence-backup.py` |
| List backups | `ls -l /var/backups/fleet-licence` |

## Buyer IPs and the rate limit

Each IP address may make 60 requests a minute. Behind Caddy, the server reads the buyer's real IP from the
`X-Forwarded-For` header, but **only when the request comes from Caddy on the same machine (127.0.0.1)**. It uses
the address Caddy added (the last one in the header), so a buyer can't dodge the limit by sending a fake header.
Requests from anywhere else are judged by their real connection address.
