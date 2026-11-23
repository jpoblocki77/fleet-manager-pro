# Shipping an update to everyone

Every copy of Fleet Manager Pro asks this server once a day (and at start-up) whether a newer build exists for its
update channel. The server answers with an **Ed25519-signed manifest** (version + SHA-256 + filename), signed with
`host_key.json`. The user sees an **"Update available"** banner and clicks **Update now** (or it installs by itself
when nobody is driving, if they turned that on); the app downloads the package over the pinned HTTPS channel, checks
it against the signed checksum, swaps the files in (keeping their `data` folder) and relaunches.

There is one build for everyone: the public copy, on the public channel. Copies sold before 1.5.29 each have their own
channel; the same package is published there too, and once installed they are on the public channel.

## Shipping a new version

1. Run the tests and bump `Fleet Manager Pro\VERSION` (e.g. `1.5.29-beta` → `1.5.30-beta`).
2. Double-click **`Ship Update.bat`** in the Development folder and type one line saying what's new. It builds the
   copy once, publishes it to every channel in `updates\` here, makes the installer and packs
   `Build Output\updates-for-cloud-<version>-<date>.zip`.
3. Upload that zip to the cloud box (AnyDesk / Remote Utilities) and unzip it into this folder there. It only adds
   files under `updates\`; there's no restart.

`python "Ship Update.py" --dry-run` shows what it would do; `--only-public` skips the older buyer channels.
To publish one package by hand: `python publish_update.py --zip "...\For Everyone.zip" --public --notes "..."`.

## Telling a bad version to update ("Please update")

If a version turns out to have a serious bug, mark it so every copy still on it shows a red **Please update**
banner on every page (with **Update now** for the Owner and Managers once a fixed version is published):

    python update_notice.py 1.5.30-beta "Jobs can be paid twice in this version. Please update now."
    python update_notice.py "<1.5.30" "This version is too old for the online features. Please update."
    python update_notice.py                      (list them)
    python update_notice.py --clear 1.5.30-beta  (remove one)    --clear-all  (remove them all)

It writes `updates/notices.json`. The server re-reads it when it changes (no restart); copies see it at their next
check (start-up or within a day). For the cloud server, upload `updates/notices.json` to the same place there.
It is only a message: nothing is locked or switched off. Copies older than 1.5.30 don't show it.

## Safety notes

- The manifest and the download are both verified against the key baked into each copy, so a broken or hostile
  server can't push a tampered build — a bad signature or checksum is refused and nothing is changed.
- A package is only offered on the channel it was published to (folders are keyed by build tag).
- Keep `host_key.json` private. It signs every update; anyone with it could make one every copy would install.
