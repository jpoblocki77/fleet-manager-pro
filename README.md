# Fleet Manager Pro - Development (seller folder)

Everything for Fleet Manager Pro in one place: the development copy, the licence server, the build scripts and every
guide. This folder is `D:\Fleet Manager Pro\Fleet Manager Pro RC 1.3\Development`. **Never share this folder.** Full instructions: **Guides\Fleet Manager Pro - Seller Guide.pdf**.

| Here | What it is | Share it? |
|---|---|---|
| `Fleet Manager Pro\` | **Development copy**: source (`fmpro\`), tests (`tests\`), tools (`tools\`). Change and test here. | **No** |
| `Fleet Manager License Host\` | **Licence server + License Manager** with your signing key (`host_key.json`), admin token (`server_config.json`), purchases (`licenses.db`) and certificate key (`tls_key.pem`). See its README. | **Never** |
| `Build Buyer Copy.bat` | **Easiest way to make a buyer's copy**: asks for the purchase key and the version, then runs the build scripts below with `--buyer`. | No |
| `Buyer Builds (PRIVATE).csv` | Written by every buyer build: date, purchase key, hidden build tag, version, zip. Your record of which copy went to whom. | **Never** |
| `Build Pro Buyer Copy (no exe).py` | Builds `..\Build Output\For Buyer (no exe)\` and its `.zip`: the antivirus-friendly buyer copy (signed Python runtime + the app compiled to native code with Nuitka). | No |
| `Build Pro Buyer Copy.py` | Builds `..\Build Output\For Buyer.zip`: the single-program `Fleet Manager Pro.exe` version (a Nuitka standalone build). | No |
| `Guides\` | Installation Guide (goes to buyers, already inside the buyer zips), Seller Guide and the licence-server guides. | Installation Guide only |

Both buyer copies are compiled to native machine code with **Nuitka** (no Python source or bytecode to recover),
contain no data and no secrets, ship `Licence Agreement.txt`, check `https://fleetmanager.servehttp.com:8750` pinned
to the server's certificate, and can't be switched to another licence server.

**Build PC needs once:** `python -m pip install nuitka` and Visual Studio (or its Build Tools) with *Desktop
development with C++*. The no-exe build takes about 20 seconds, the .exe build about 3 minutes.

## Copyright and licence agreement

Fleet Manager Pro is © 2026 Jeff Poblocki. All rights reserved. The licence agreement is
`Fleet Manager Pro\fmpro\EULA.txt`: it ships with both buyer copies as **Licence Agreement.txt**, is readable in the
app at `/licence-agreement` (linked from every page) and buyers must tick **I agree** to activate. Edit that one file to
change the terms, then rebuild. Only ever send the two *For Buyer* zips: `Fleet Manager Pro RC 1.3.zip` and the
*Source Code* zip contain the readable source.

## Making a sale

1. Licence server running on the server PC (192.168.1.29).
2. License Manager → **2) New purchase** → key `FMP-XXXX-XXXX-XXXX-XXXX` (2 PCs).
3. Send the buyer **`For Buyer (no exe).zip`** and the key.

## After changing the development copy

```
python tests\e2e_test.py
python tests\security_test.py
python tests\fuzz_test.py
```

All three must say **ALL PASSED**. Then build both buyer copies, refresh the guides (`python tools\make_install_guide.py`
and `python tools\make_seller_guide.py`) and copy the program files to `..\App` (never its `data` folder), `..\Source`, `..\For Buyer (no exe)` and `..\Zips`.
After a game update or new DLC run `python tools\build_cargo.py`.

## The packaged release on D:

`D:\Fleet Manager Pro\Fleet Manager Pro RC 1.3\` holds your running copy (`App\`, with your company data), the
entire source without keys or data (`Source\`), the no-exe buyer copy, the guides, a private backup of the licence host
and all four zips. Its `READ ME FIRST.txt` explains each folder.

## Guides

| PDF | For |
|---|---|
| Fleet Manager Pro - Installation Guide | Buyers: install, activate, plugin, first delivery, drivers, recruiting, leasing, data, troubleshooting |
| Fleet Manager Pro - Seller Guide | You: folders, sales, licences, updating, licence server, secrets |
| Running the Licence Server at Home | You: No-IP, router port 8750, firewall, encryption (written for the original app - see Seller Guide section 7) |
| Resetting a Licence's Uses | You: when a buyer can't deactivate a PC |
| Kamatera Hosting Guide | You, later: move the licence server to a cloud server |
| Putting Fleet Manager on GitHub | You: keep the project privately on GitHub |
| Older (classic Fleet Manager)\ | Guides for the original Fleet Manager, kept for reference |
