# Fleet Manager Pro — Fleet Manager server + License Manager (author only)

**Never send this folder to anyone.** `host_key.json` is your private signing key, `server_config.json` holds
your admin token, `licenses.db` is the server's database and `tls_key.pem` is the server's secret encryption key.
Back all four up.

Fleet Manager Pro is **free**: no licence keys and no activation. This server gives every copy its **updates** and
hosts the **online features**: each company's Apply page, the Global Leaderboard and the VTC Finder. It still answers
copies sold before 1.5.29 (with `FMP-` keys) so they can update and hand their online data over.

| File | What it is |
|---|---|
| `License Server.bat` / `license-server.sh` / `license_server.py` | the Fleet Manager server (port 8750). Must be running and reachable. |
| `License Manager.bat` / `license-manager.sh` / `license_tool.py` | your admin tool: block or unblock a company, VTC Finder listings, the old purchases |
| `publish_update.py` | puts a built package into `updates\` (Ship Update.bat in the Development folder runs it for you) |
| `update_notice.py` | marks a version bad, so copies on it show a red **Please update** banner (see UPDATES.md) |
| `Reset Licences.bat` / `reset_licences.py` | wipes every old purchase (backs up `licenses.db` first, you type RESET) |
| `host_key.json` | private signing key (secret): signs every update |
| `server_config.json` | admin token, port, public address |
| `licenses.db` | companies' online IDs, Apply pages and waiting applications, the leaderboard, VTC Finder profiles, old purchases |
| `tls_cert.pem` / `tls_key.pem` | the server's certificate and its **secret** key (encryption, valid until 2046) |
| `updates\` | one folder per update channel (the public one and one per older buyer copy), and `notices.json` |
| `deploy\` | ready-made files to put the server on Kamatera: Prepare Upload.bat, then `install.sh` (see `deploy\README.md`) |

## How companies are known online

The first time a company uses the Apply page, Global Leaderboard or VTC Finder, its copy makes a random **company ID**
(`FMC-XXXX-XXXX-XXXX-XXXX`) and a secret, kept in its own database, and registers them here (at most 20 new
companies per internet address a day). Every sync proves it with the secret. A copy that was activated with a key
before 1.5.29 also sends that key once, and its Apply page, leaderboard entry and VTC Finder profile move to the
company ID.

* **Block a company** (License Manager → **5**): type its company ID, or an old purchase key to block every company
  that came from it. It loses the online features; the app itself keeps working. **6** unblocks.
* **VTC Finder listings** (License Manager → **11**): hide or show a company, or forget its reports.
* The old purchase options (2, 3, 4, 7, 8, 9, 10) only matter for copies sold before Fleet Manager Pro went free.

## Your setup (cloud server)

| | |
|---|---|
| Server | cloud box, reached over **AnyDesk / Remote Utilities**; runs **License Server.bat** (keep its window open) |
| Internet address | `fleetmanager.servehttp.com` (free No-IP name pointing at the cloud box) |
| Port | inbound **TCP 8750** open on the cloud box's firewall |
| Every copy checks | `https://fleetmanager.servehttp.com:8750`, pinned to `tls_cert.pem` |
| Admin (License Manager) | runs **on the cloud box itself** — `admin_url` is `http://127.0.0.1:8750` |

**Updating the server:** copy `license_server.py`, `license_tool.py`, `reset_licences.py`, the `.bat` files and this
README over the old ones on the cloud box, then close and reopen License Server.bat. Never copy `licenses.db`,
`host_key.json`, `server_config.json` or the `.pem` files over the server's own - those hold its keys and purchases.

**Admin is local only:** License Manager's functions only work from the server itself, never straight from the
internet. AnyDesk / Remote Utilities into the cloud box and run License Manager there.

To move to a cloud server later: **Guides\Kamatera Hosting Guide.pdf** and `deploy\`.

## Encryption (HTTPS on port 8750)

With `tls_cert.pem` and `tls_key.pem` in this folder, the licence server speaks HTTPS on port 8750. Copies are
*pinned* to this exact certificate, so nobody in between can read purchase keys or fake the server.

- `tls_key.pem` is secret: back it up with `host_key.json`. If you lose it, new copies need a new certificate.
- License Manager **1) Set up** with an `https://` address writes the address, your public key and the certificate pin
  into a Fleet Manager Pro folder's `fmpro\licensing.py` (the build scripts already contain them).
- The certificate is valid until 2046.

## On Linux (Ubuntu 24.04)

The `.sh` files do the same as the `.bat` files. Ubuntu 24.04 already has Python 3.12.

```bash
chmod +x license-manager.sh license-server.sh   # first time only
./license-manager.sh                            # menu (same as License Manager.bat)
./license-manager.sh list                       # or any command: new, list, release, revoke, restore, reset
./license-server.sh --host 127.0.0.1            # run the server (behind Caddy, as in the Kamatera guide)
```

If you copy the files over from Windows, keep the `.sh` files' Unix (LF) line endings; don't save them in Notepad.

## Commands

```
python license_tool.py                                   menu
python license_tool.py new "Name" [email]                new purchase -> FMP-XXXX-... (2 PCs)
python license_tool.py list                              purchases and their PCs
python license_tool.py release KEY FMID-...              free one PC slot
python license_tool.py revoke KEY  |  restore KEY        block / unblock a purchase on every PC
python license_tool.py reset KEY                         free ALL PCs on a purchase
python license_tool.py setup --app "PATH" --url URL      signing key + admin token; point a Fleet Manager Pro folder at URL
```

