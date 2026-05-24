# Multi Roblox Manager — C# rewrite (MVP)

WPF on .NET 8. Same Windows-only constraints as the Python branch
(holds the `ROBLOX_singletonEvent` mutex, per-account DPAPI-encrypted
cookies, ticket auth, launch via the official Roblox launcher or
direct exec). This is a **work in progress**: the core launch / focus
/ close / server-hop flow is implemented; several features from the
Python branch are stubbed for follow-up sessions.

## Status

| Feature                                | Status |
| -------------------------------------- | ------ |
| Singleton mutex for multiple instances | ✅ done |
| Per-account `.ROBLOSECURITY` storage   | ✅ DPAPI |
| Auth ticket + cookie validation        | ✅ done |
| Direct `RobloxPlayerBeta.exe` launch   | ✅ done |
| Protocol launch via `RobloxPlayerLauncher.exe` | ✅ done |
| Find + focus window per instance       | ✅ done |
| Server hop (overlap)                   | ✅ done |
| Bulk select + close                    | ✅ done |
| Stats polling (RAM, crashed detection) | ✅ RAM done; CPU% TODO |
| Dark theme + DPI                       | ✅ native WPF + dark titlebar |
| Per-account proxy (HTTP/SOCKS)         | ✅ done (.NET native SOCKS) |
| Persisted presets                      | ✅ done |
| Anti-AFK                               | ⏳ TODO |
| Per-account `LOCALAPPDATA` isolation   | ⏳ TODO |
| Browser sign-in (WebView2)             | ⏳ TODO |
| Discord webhook on crash               | ⏳ TODO |
| Discord bot + screenshot               | ⏳ TODO |

## Build

Needs the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)
on a Windows machine.

```powershell
.\build.bat
```

Produces `dist\MultiRobloxManager.exe` — a self-contained, single-file
~80 MB exe with the .NET runtime bundled. No installer, no .NET
dependency on the destination machine. Startup is ~300 ms.

## Project layout

```
MultiRobloxManager.sln
MultiRobloxManager/
  MultiRobloxManager.csproj
  app.manifest                 PerMonitorV2 DPI + asInvoker
  App.xaml(.cs)                merges Themes/Dark.xaml + crash handler
  MainWindow.xaml(.cs)         menu + launch card + tree + actions
  Models/
    Account.cs                 user id, DPAPI cookie, optional proxy
    Config.cs                  presets, theme, mode, webhook, bot
    Instance.cs                running client (INotifyPropertyChanged)
    Preset.cs
  Services/
    AccountStore.cs            JSON persistence + AddOrUpdateAsync
    AppPaths.cs                %APPDATA%\MultiRobloxManager\…
    Auth.cs                    CSRF + ticket + PlaceLauncher URL
    ConfigStore.cs             JSON persistence + preset / place helpers
    Dpapi.cs                   ProtectedData wrapper
    InstanceManager.cs         orchestrates launch / focus / hop / stats
    Launcher.cs                find exes + spawn (direct / protocol)
    Logging.cs                 rolling text log @ %APPDATA%/.../logs/
    Servers.cs                 server list + place id parsing
    SingletonMutex.cs          ROBLOX_singletonEvent holder
  Win32/
    NativeMethods.cs           P/Invoke for user32/kernel32/dwmapi
    WindowHelpers.cs           PID list / window find / focus / kill
  Themes/
    Dark.xaml                  palette + control styles
  Dialogs/
    AccountManagerDialog.xaml(.cs)
build.bat
```

## Data locations

Same as the Python branch — both can coexist on the same machine.

- `%APPDATA%\MultiRobloxManager\accounts.json` — accounts (DPAPI blob).
- `%APPDATA%\MultiRobloxManager\config.json` — presets, theme, mode.
- `%APPDATA%\MultiRobloxManager\logs\manager.log` — rolling log.

## Why this rewrite

Mostly a faster, smaller distribution: ~80 MB self-contained vs the
Python build's ~200 MB folder; ~300 ms cold start vs ~1–2 s. Same
functionality where the work has been done; same Windows-only
limitations.
