# Fleet Manager Pro - Development (author's folder)

Everything for Fleet Manager Pro in one place: the development copy, the Fleet Manager server, the build scripts and
every guide. Fleet Manager Pro is **free**: one public copy for everyone, no licence keys, no activation. This folder
is `D:\Fleet Manager Pro\Fleet Manager Pro RC 1.5.29-beta\Development`. **Never share this folder.** Full
instructions: **Guides\Fleet Manager Pro - Author Guide.pdf**.

| Here | What it is | Share it? |
|---|---|---|
| `Fleet Manager Pro\` | **Development copy**: source (`fmpro\`), tests (`tests\`), tools (`tools\`). Change and test here. | **No** |
| `Fleet Manager License Host\` | **Fleet Manager server + License Manager** with your signing key (`host_key.json`), admin token (`server_config.json`), database (`licenses.db`) and certificate key (`tls_key.pem`). See its README. | **Never** |
| `Ship Update.bat` | **Ships a new version to everyone in one go**: builds the public copy once, publishes it to every update channel, makes the installer (and copies it to the website's `downloads` folder) and packs `..\Build Output\updates-for-cloud-<version>-<date>.zip` to upload to the cloud server. | No |
| `Build Public Copy.bat` | Just builds the public copy and its installer, without publishing anything. | No |
| `Build Public Copy.py` | Builds `..\Build Output\For Everyone\` and `For Everyone.zip`: a single `Fleet Manager Pro.exe` (Nuitka). | No |
| `Build Installer.py` | Makes `..\Build Output\Installers\Public\Fleet Manager Pro Setup.exe` from the built copy. Needs Inno Setup 6. | No |
| `Buyer Builds (PRIVATE).csv` | The copies built for buyers before Fleet Manager Pro went free. Ship Update still publishes to their channels so they move over. | **Never** |
| `Guides\` | Website pages (install.html, features.html, faq.html), `READ ME - Your Data.txt` (ships in every copy), Installation Guide (ships in every copy), Author Guide, Publishing an Update and the older server guides. | Installation Guide and the web pages |

The public copy is compiled to native machine code with **Nuitka** (no Python source or bytecode to recover),
contains no data and no secrets, ships `Licence Agreement.txt`, talks to `https://fleetmanager.servehttp.com:8750`
pinned to the server's certificate, and only installs updates signed by the server's key.

**Build PC needs once:** `python -m pip install nuitka`, Visual Studio (or its Build Tools) with *Desktop development
with C++*, and Inno Setup 6. A build takes about 2-3 minutes.

## Copyright and licence agreement

Fleet Manager Pro is © 2026 Jeff Poblocki. All rights reserved. The licence agreement is
`Fleet Manager Pro\fmpro\EULA.txt`: it ships as **Licence Agreement.txt**, the installer shows it, and the app opens on
it until **I agree** is ticked (also readable at `/licence-agreement`, linked from every page). Edit that one file to
change the terms, then ship a new version.

## Shipping a new version

```
python tests\e2e_test.py
python tests\security_test.py
python tests\fuzz_test.py
```

All three must say **ALL PASSED**. Then bump `Fleet Manager Pro\VERSION`, double-click `Ship Update.bat`, upload the
`updates-for-cloud-...zip` it makes to the cloud server (unzip into its License Host folder), upload the website's new
`downloads\Fleet Manager Pro Setup.exe`, refresh the guides (`python tools\make_install_guide.py`,
`make_install_page.py`, `make_features_page.py`, `make_author_guide.py`), commit and push.
After a game update or new DLC run `python tools\build_cargo.py`.

A version with a serious bug: `python update_notice.py 1.5.30-beta "message"` in the License Host folder, then upload
`updates\notices.json`. Copies on that version show a red **Please update** banner.

## The packaged release on D:

`D:\Fleet Manager Pro\Fleet Manager Pro RC 1.5.29-beta\` holds this Development folder, `Build Output\`, the guides,
database backups and a private backup of the server folder. Its `READ ME FIRST.txt` explains each folder. The source
code is here and in the private GitHub repo. The website is `D:\Fleet Manager Pro\Website`.

## Guides

| PDF | For |
|---|---|
| Fleet Manager Pro - Installation Guide | Everyone: install, licence agreement, plugin, first delivery, drivers, recruiting, leasing, data, troubleshooting |
| Fleet Manager Pro - Author Guide | You: folders, shipping, the website, the online features, the server, secrets |
| Fleet Manager Pro - Publishing an Update | You: how updates reach everyone, step by step, and "Please update" notices |
| Running the Licence Server at Home | You: No-IP, router port 8750, firewall, encryption (written for the original app) |
| Resetting a Licence's Uses | Only for copies sold before Fleet Manager Pro went free |
| Kamatera Hosting Guide | You, later: move the server to another cloud host |
| Putting Fleet Manager on GitHub | You: keep the project privately on GitHub |
| Older (classic Fleet Manager)\ | Guides for the original Fleet Manager, kept for reference |
