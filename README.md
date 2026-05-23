# Multi Roblox Manager

A Windows desktop tool that lets you run several Roblox clients side-by-side,
each signed in to a different account, with one-click focus switching and
server hopping that doesn't trip the "you are joining too quickly" errors.

> Windows only. Roblox's client is Windows-native; the techniques here
> (Win32 mutex hold, DPAPI, `RobloxPlayerBeta.exe` direct exec) don't have
> macOS or Linux equivalents.

## Features

- **Multiple instances**: holds the `ROBLOX_singletonEvent` named mutex for
  the lifetime of the app so each new launch bypasses the "Roblox is
  already running" exit path.
- **Multi-account, properly**: each instance authenticates with its own
  one-shot ticket minted from a saved `.ROBLOSECURITY` cookie. Different
  instances run as different accounts at the same time, regardless of
  which account the Roblox launcher is signed into.
- **Stable launches via the official launcher (default)**: opens a
  `roblox-player:1+launchmode:play+gameinfo:<ticket>+placelauncherurl:…`
  URL so `RobloxPlayerLauncher.exe` runs updates / sets up Hyperion's
  expected parent process, then spawns `RobloxPlayerBeta.exe` itself with
  the same ticket. Multi-account still holds — the ticket carries the
  identity. A **Direct** mode that runs `RobloxPlayerBeta.exe` itself is
  available for users who don't want the launcher in the loop, and the
  protocol path auto-falls-back to direct exec if it stalls.
- **Persistent presets**: launch configurations (label + place + account)
  are saved to disk and survive restarts. "Launch All" boots them in one
  click, spaced to avoid join throttling.
- **Encrypted cookie storage**: cookies are encrypted with DPAPI
  (`CryptProtectData`) so the on-disk file is only decryptable by your
  Windows user account.
- **Per-account profile isolation**: every launch overrides
  `LOCALAPPDATA` to a per-account directory under
  `%APPDATA%\MultiRobloxManager\data\<user_id>\`. Cookies, cache, and
  logs Roblox writes stay scoped to that account, reducing fingerprint
  linkage. The real `Versions\` binary directory is exposed via an NTFS
  junction so the launcher still finds the player exe.
- **Per-account proxy**: each account can carry an `http://` or
  `socks5://` proxy URL applied to Roblox's auth-ticket / identity calls.
  (Game-client traffic still goes direct unless you also use a system
  proxy — there's no per-process network namespace on Windows.)
- **CPU/RAM stats + crash detection**: every running client is polled
  via `psutil` ~1.5 s and shown in the table; if a PID disappears the
  row turns red and the status flips to "crashed".
- **Bulk actions**: Shift/Ctrl-click multiple rows to focus, hop, or
  close them in one go.
- **Rotating log file**: everything important goes to
  `%APPDATA%\MultiRobloxManager\logs\manager.log` (2 MB × 5 backups);
  there's an **Open Logs** button so you can grab it for bug reports.
- **Roblox version awareness**: the detected client version is shown in
  the status bar and logged so you can correlate breakage with updates.
- **Dark / light theme**: ships with a modern dark theme (Sun Valley via
  `sv-ttk`) on by default; **Toggle Theme** in the toolbar flips it
  and the choice persists. Title bars get the native Windows
  `DwmSetWindowAttribute` dark mode applied so the chrome matches.
- **Per-instance Anti-AFK**: each row has an *Anti-AFK* column you can
  click to toggle, plus **Enable Anti-AFK on All** / **Disable on All**
  buttons. When a tick fires we `PostMessageW` a 5–15 px mouse jitter
  around the client-area center plus an `F15` keystroke — a "dead"
  function key no game binds — straight to the background Roblox HWND,
  **at most once every 15 minutes** per instance. That's the minimum
  signal Roblox needs to reset its 20-minute idle timer; no spam.
  Skip rule: if the Roblox window is foregrounded *and* you've
  produced real keyboard/mouse input in the last 60 s (checked via
  `GetLastInputInfo`), the tick is suppressed entirely — PvP / 1v1
  safety so synthetic input can't clash with your real clicks. The
  worker still wakes every 12–35 s to check state (Win32 calls that
  cost microseconds) but only fires PostMessages on the 15-min
  cadence, so dozens of workers idle at effectively zero CPU.
- **Focus + cycle**: per-instance focus button, plus `Ctrl+Tab` to cycle.
  Uses `AttachThreadInput` + `SetForegroundWindow` to defeat focus-stealing
  prevention.
- **Overlap server hop**: a hop brings up the replacement client *first*,
  then kills the old PID once the new window appears — so the visible gap
  is just the new client's load screen rather than a close-and-reopen.

## Requirements

- Windows 10 or 11
- Roblox installed (the manager auto-discovers
  `%LOCALAPPDATA%\Roblox\Versions\version-*\RobloxPlayerBeta.exe`)
- Python 3.10+

## Setup

```powershell
git clone <this repo>
cd silver-waffle
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Building a standalone .exe

Run on a Windows machine — PyInstaller produces native binaries, so
the build has to happen on the OS you're targeting. From the repo
root:

```powershell
.\build.bat
```

This sets up a venv, installs `pyinstaller` plus the runtime deps,
and produces `dist\MultiRobloxManager.exe` — a single-file `.exe` you
can copy anywhere. No Python install required on the destination
machine.

The build uses `--onefile --windowed` (no console window) and pulls
in `pywebview` + `psutil` via `--collect-all` so the
browser-sign-in flow and the per-instance stats both work in the
bundled exe. First launch unpacks to a temp dir so startup is a
second or two slower than running from source — subsequent launches
are normal speed once Windows caches the unpack.

## Adding accounts

Two ways:

### Sign in with Browser (recommended — supports password, MFA, QR, passkey)

1. Click **Accounts…** → **Sign in with Browser…**
2. An embedded Roblox login window opens. Use *any* of Roblox's official
   sign-in methods:
   - Username + password (+ MFA if enabled)
   - **QR code** ("Sign in with another device" on Roblox's login page —
     scan with the Roblox mobile app on a signed-in phone)
   - Passkey
3. Once Roblox redirects you to the home page, the window closes and the
   harvested `.ROBLOSECURITY` cookie is DPAPI-encrypted and saved.

This needs `pywebview` (in `requirements.txt`); WebView2 runtime is
preinstalled on Windows 10/11.

### Paste cookie (no extra deps)

1. Click **Accounts…**.
2. In a logged-in browser, open DevTools → Application → Cookies →
   `https://www.roblox.com`, copy the `.ROBLOSECURITY` value.
3. Paste it into the cookie box, give it a nickname, click **Add / Update**.

Either way the cookie is validated against
`users.roblox.com/v1/users/authenticated` before being saved to
`%APPDATA%\MultiRobloxManager\accounts.json`. Cookies expire eventually —
when they do, the launcher surfaces an `Auth ticket refused` error and
you just re-run **Sign in with Browser…** (or paste a fresh cookie).

> Keep cookies private. Anyone with your `.ROBLOSECURITY` value can log in
> as you. The DPAPI wrapper means the on-disk blob can only be decrypted
> by your Windows user on your machine.

## Launching instances

1. Paste a `placeId` (e.g. `920587237`) or a full Roblox game URL
   (`https://www.roblox.com/games/920587237/Adopt-Me`).
2. Pick an account from the dropdown (or leave it on the signed-in
   fallback).
3. Optional **Label** to make instances easy to tell apart in the table.
4. **Launch**. Repeat for as many clients as you want.

Use **Save Preset** to remember a (label, place, account) combo. Presets
show up in the right-hand list and can be launched individually, all at
once, or removed.

## Server hopping

Select a running instance and click **Server Hop**:

1. Queries `games.roblox.com/v1/games/{place}/servers/Public`.
2. Picks a non-full public server whose jobId isn't in the last 10 you
   were in (avoids rejoining the same instance you just left).
3. Spawns a replacement `RobloxPlayerBeta.exe` for the same account into
   that jobId.
4. Waits for the new window to appear, **then** kills the previous PID.

Launches across the whole app are spaced by `launch_cooldown` (default
2.5s) to stay under Roblox's per-account join rate limit. Tune it in
`%APPDATA%\MultiRobloxManager\config.json` if needed.

## How "no errors" is actually achieved

| Error people see                          | What the manager does to avoid it                                                              |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------- |
| "Roblox is already running"               | Holds `ROBLOX_singletonEvent` so the second client skips that branch.                          |
| Rate-limit / "joining too quickly" / 729  | `LAUNCH_COOLDOWN` between any two launches; recent-jobId blocklist when hopping.               |
| "Authentication failed" on extra clients  | Per-account auth ticket → `gameinfo:<TICKET>` in the protocol URL; the launcher's signed-in account is irrelevant. |
| Close-and-reopen flash on hop             | Overlap hop: new client up before old PID is killed.                                           |
| Hyperion / launcher-update flakiness      | Default `protocol` mode goes through `RobloxPlayerLauncher.exe`; if it stalls, the manager refetches a ticket and falls back to direct exec automatically. |

## File layout

```
main.py
multi_roblox/
  accounts.py        # AccountStore (DPAPI-encrypted cookies, proxy per account)
  antiafk.py         # Per-instance Anti-AFK ticker (PostMessageW, no focus steal)
  auth.py            # CSRF + authentication-ticket exchange (proxy-aware)
  browser_login.py   # Embedded webview sign-in (password / QR / passkey)
  config.py          # Persistent presets / recent places / cooldown / mode
  dpapi.py           # CryptProtectData / CryptUnprotectData wrappers
  gui.py             # Tkinter GUI
  launcher.py        # Find launcher + player exes; spawn with env overrides
  logging_setup.py   # Rotating log file + console handler
  manager.py         # InstanceManager — mutex, launch, focus, hop, stats
  mutex.py           # SingletonMutex (ROBLOX_singletonEvent)
  profiles.py        # Per-account LOCALAPPDATA isolation (mklink /J)
  servers.py         # Public server list, jobId picker, protocol URI builder
  stats.py           # psutil-based CPU/RAM/liveness sampling
  windows.py         # Win32 helpers: PID list, window find/focus, kill
```

## Data locations

- `%APPDATA%\MultiRobloxManager\accounts.json` — accounts (cookies
  encrypted) + per-account proxy URL.
- `%APPDATA%\MultiRobloxManager\config.json` — presets, recent places,
  launch cooldown, launch mode.
- `%APPDATA%\MultiRobloxManager\data\<user_id>\` — per-account isolated
  Roblox `LOCALAPPDATA`. A `Roblox\Versions` junction inside points back
  at the real binary directory.
- `%APPDATA%\MultiRobloxManager\logs\manager.log` — rotating log file.

## Known limitations / deliberately not built

- **CustomTkinter port** — pure cosmetics; not done in this branch.
- **Encrypted account export/import** — needs a passphrase-based key
  derivation distinct from DPAPI (which is per-machine); future work.
- **Per-instance VPN/proxy for game traffic** — Roblox's client doesn't
  honor in-process proxy settings; that requires a system-level tool
  like a per-process firewall rule, proxifier, or a WireGuard tunnel.
  Per-account proxy here covers the auth / API calls only.

## Use responsibly

This tool is for managing your own accounts. Don't use it to violate
Roblox's Terms of Service. Auth tickets minted from a stolen cookie are
still a stolen account.
