# Fleet Manager Pro — Licence server + License Manager (seller only)

**Never send this folder to anyone.** `host_key.json` is your private signing key, `server_config.json` holds
your admin token, `licenses.db` is your list of purchases and `tls_key.pem` is the server's secret encryption key.
Back all four up.

This server licenses **Fleet Manager Pro only**: it issues `FMP-` keys and refuses any other product.

| File | What it is |
|---|---|
| `License Server.bat` / `license-server.sh` / `license_server.py` | the online licence server (port 8750). Must be running and reachable. It also hosts each company's online **Apply** page for recruitment. |
| `License Manager.bat` / `license-manager.sh` / `license_tool.py` | your admin tool: set up, new purchases, list PCs, release, revoke, reset |
| `Reset Licences.bat` / `reset_licences.py` | wipes every purchase (backs up `licenses.db` first, you type RESET) - only for starting over |
| `host_key.json` | private signing key (secret) |
| `server_config.json` | admin token, port, lease length (7 days), public address |
| `licenses.db` | purchases and the PCs activated on each (max 2), plus waiting online job applications |
| `tls_cert.pem` / `tls_key.pem` | the server's certificate and its **secret** key (encryption, valid until 2046) |
| `deploy\` | ready-made files to put the server on Kamatera: Prepare Upload.bat, then `install.sh` (see `deploy\README.md`) |

## Selling a copy

1. Build the buyer copy (in the folder above this one): `Build Buyer Copy.bat`, or
   `python "Build Pro Buyer Copy.py" --buyer FMP-...`, makes `For Buyer - FMP-....zip` (a single `Fleet Manager Pro.exe`).
   It is compiled (no readable source), contains no data and no secrets, checks
   `https://fleetmanager.servehttp.com:8750` pinned to `tls_cert.pem`, and can't be pointed at another licence server.
2. **License Manager → 2) New purchase**: type the buyer's name (email optional). You get a key `FMP-XXXX-XXXX-XXXX-XXXX`
   that works on 2 PCs. Command line: `python license_tool.py new "Name" [email]`.
3. Send the buyer the zip and the key. They unzip it, double-click **Start Fleet Manager Pro.bat** and type the key on the
   **Activate** screen. The Installation Guide PDF is inside the zip.

## How online licensing works

1. The buyer types the key on Fleet Manager Pro's **Activate** screen. It sends key + Machine ID to the licence server,
   which allows **at most 2 PCs per purchase** and returns a lease signed with your private key.
2. Fleet Manager Pro re-checks online at start-up and every 6 hours. If you **revoke** the purchase or **release** a PC,
   that PC locks at its next check. If the server is unreachable it keeps working on its last lease for up to 7 days,
   then locks until it can check again. Setting the PC clock back also locks it.
3. Buyers can free a PC themselves: Settings → Licence → **Deactivate this PC**.
4. If a buyer **can't** deactivate (PC died, Windows reinstalled, PC sold): License Manager → **7) Reset a purchase's
   uses**. It shows which PCs are in use, asks you to type YES, then frees every PC on that purchase. Old PCs lock at
   their next check. Each reset is counted and shown in **3) List purchases** (e.g. "reset 2x"), so you can spot abuse.
   Command line: `python license_tool.py reset FMP-XXXX-…`. To free just one known PC, use **4) Release a PC**.
5. A key for any other product, or a wrong key, is refused **before** it uses a PC slot.
6. **Delete a purchase** for good (License Manager → **8) Delete a purchase**, active or revoked; type DELETE). Its PCs,
   its online Apply page and any waiting applications go too; copies using the key lock at their next check and the
   key never works again. **9) Delete ALL revoked purchases** clears out every revoked one at once. Before anything is
   deleted, a copy is appended to `deleted_purchases.jsonl` (private: it has buyers' names and emails). Command line:
   `python license_tool.py delete FMP-XXXX-…` or `python license_tool.py delete-revoked`. To block a key but keep its
   record, use **5) Revoke** instead.

Leases are signed with your private key and Fleet Manager Pro only trusts your public key, so a fake server can't
unlock it.

## Your setup (home server)

| | |
|---|---|
| Server PC | `192.168.1.29`, runs **License Server.bat** |
| Internet address | `fleetmanager.servehttp.com` (free No-IP name; keep the No-IP DUC running on the server PC) |
| Router | forwards **TCP 8750 only** to 192.168.1.29 (Spectrum: My Spectrum app → Advanced Settings → Port Forwarding) |
| Buyer copies check | `https://fleetmanager.servehttp.com:8750`, pinned to `tls_cert.pem` |
| Your own copies also try | `https://192.168.1.29:8750` (routers rarely loop the public address back inside your network) |
| Windows Firewall (server PC) | inbound TCP 8750 allowed (rule "Fleet Manager licence server") |

**Updating the server PC:** copy `license_server.py`, `license_tool.py`, `reset_licences.py`, the `.bat` files and this
README over the old ones on 192.168.1.29, then close and reopen License Server.bat. Never copy `licenses.db`,
`host_key.json`, `server_config.json` or the `.pem` files over the server's own - those hold its keys and purchases.

**Admin is local only:** License Manager's functions only work from the server PC itself or your home network, never
straight from the internet. Run License Manager on the server PC.

To move to a cloud server later: **Guides\Kamatera Hosting Guide.pdf** and `deploy\`.

## Encryption (HTTPS on port 8750)

With `tls_cert.pem` and `tls_key.pem` in this folder, the licence server speaks HTTPS on port 8750. Buyer copies are
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

## Buyer copies are sealed to their buyer

Buyer copies are built per purchase (`Build Buyer Copy.bat` in the Development folder). Each copy has a hidden build
tag and its pages are encrypted; on activation and every check this server returns the page key inside the signed
lease (wrapped to the PC). **The server PC must run this version of `license_server.py`, or new copies stay locked.**
The page keys come from `host_key.json`, so nothing else needs copying.

* **3) List** shows `! LEAK?` under a PC that runs a copy built for a different purchase.
* **10) Identify a copy** (or `python license_tool.py whose FMPB-...`) tells you whose copy a build tag is. Get the tag
  from a copy with `"Fleet Manager Pro.exe" --build-tag` or `runtime\python.exe app\run.pyc --build-tag`.
