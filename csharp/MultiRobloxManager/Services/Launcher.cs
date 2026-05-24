using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;

namespace MultiRobloxManager.Services;

internal static class Launcher
{
    private static FileInfo? NewestUnderVersions(string filename)
    {
        var versionsDir = Path.Combine(AppPaths.LocalAppData, "Roblox", "Versions");
        if (!Directory.Exists(versionsDir)) return null;
        FileInfo? newest = null;
        foreach (var dir in Directory.EnumerateDirectories(versionsDir))
        {
            var path = Path.Combine(dir, filename);
            if (!File.Exists(path)) continue;
            var fi = new FileInfo(path);
            if (newest is null || fi.LastWriteTimeUtc > newest.LastWriteTimeUtc) newest = fi;
        }
        return newest;
    }

    public static FileInfo? FindPlayerExe()   => NewestUnderVersions("RobloxPlayerBeta.exe");
    public static FileInfo? FindLauncherExe() => NewestUnderVersions("RobloxPlayerLauncher.exe");

    /// <summary>Returns e.g. "version-abc123" if discoverable, else null.</summary>
    public static string? DetectVersion()
    {
        var exe = FindPlayerExe();
        if (exe is null) return null;
        var parent = exe.Directory?.Name ?? "";
        return parent.StartsWith("version-", StringComparison.OrdinalIgnoreCase)
            ? parent.Substring("version-".Length)
            : parent;
    }

    /// <summary>Build the command-line args for direct RobloxPlayerBeta.exe exec with a ticket.</summary>
    public static List<string> BuildArgs(string ticket, long placeId, string? jobId, long browserTrackerId)
    {
        var placeUrl = Auth.PlaceLauncherUrl(placeId, jobId,
            browserTrackerId == 0 ? null : browserTrackerId);
        return new List<string>
        {
            "--play",
            "-a", "https://www.roblox.com/Login/Negotiate.ashx",
            "-t", ticket,
            "-j", placeUrl,
            "-b", browserTrackerId.ToString(),
            $"--launchtime={Auth.LaunchTimeMs()}",
            "--rloc", "en_us",
            "--gloc", "en_us",
        };
    }

    /// <summary>
    /// Spawn RobloxPlayerBeta.exe directly. Faster start, but skips the
    /// official launcher's update check and Hyperion parent-process setup.
    /// </summary>
    public static Process LaunchDirectWithTicket(string ticket, long placeId, string? jobId,
        IDictionary<string, string?>? extraEnv = null)
    {
        var exe = FindPlayerExe()
            ?? throw new FileNotFoundException(
                "RobloxPlayerBeta.exe not found. Install Roblox and run it once.");
        long bti = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) & 0x7FFFFFFF;
        var args = BuildArgs(ticket, placeId, jobId, bti);
        var psi = new ProcessStartInfo
        {
            FileName = exe.FullName,
            WorkingDirectory = exe.DirectoryName!,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);
        ApplyEnv(psi, extraEnv);
        var p = Process.Start(psi)!;
        Log.Info("spawn (direct): pid={0}", p.Id);
        return p;
    }

    /// <summary>
    /// Build the roblox-player: protocol URL with the auth ticket embedded
    /// as gameinfo. RobloxPlayerLauncher.exe handles updates and Hyperion's
    /// parent-process expectations.
    /// </summary>
    public static string ProtocolUrlWithTicket(string ticket, long placeId, string? jobId, long browserTrackerId)
    {
        var placeUrl = Auth.PlaceLauncherUrl(placeId, jobId,
            browserTrackerId == 0 ? null : browserTrackerId);
        // Percent-encode (Uri.EscapeDataString matches Python's
        // urllib.parse.quote default behavior); WebUtility.UrlEncode would
        // turn spaces into `+` which is form-encoding, not what we want here.
        var encodedPlace = Uri.EscapeDataString(placeUrl);
        var parts = new[]
        {
            "roblox-player:1",
            "launchmode:play",
            $"gameinfo:{ticket}",
            $"launchtime:{Auth.LaunchTimeMs()}",
            $"placelauncherurl:{encodedPlace}",
            $"browsertrackerid:{browserTrackerId}",
            "robloxLocale:en_us",
            "gameLocale:en_us",
        };
        return string.Join("+", parts);
    }

    /// <summary>
    /// Open the protocol URL via RobloxPlayerLauncher.exe so we can override
    /// env vars (LOCALAPPDATA for per-account isolation, when wired up).
    /// </summary>
    public static Process LaunchProtocolWithTicket(string ticket, long placeId, string? jobId,
        IDictionary<string, string?>? extraEnv = null)
    {
        long bti = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) & 0x7FFFFFFF;
        var url = ProtocolUrlWithTicket(ticket, placeId, jobId, bti);
        var launcherExe = FindLauncherExe();
        if (launcherExe is not null)
        {
            var psi = new ProcessStartInfo
            {
                FileName = launcherExe.FullName,
                WorkingDirectory = launcherExe.DirectoryName!,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.ArgumentList.Add(url);
            ApplyEnv(psi, extraEnv);
            var p = Process.Start(psi)!;
            Log.Info("spawn (protocol via launcher): pid={0}", p.Id);
            return p;
        }
        // Fallback: hand off through the shell (no env override possible).
        var shellPsi = new ProcessStartInfo
        {
            FileName = url,
            UseShellExecute = true,
        };
        var sp = Process.Start(shellPsi)!;
        Log.Info("spawn (protocol via shell handoff): pid={0}", sp?.Id ?? -1);
        return sp!;
    }

    private static void ApplyEnv(ProcessStartInfo psi, IDictionary<string, string?>? extraEnv)
    {
        if (extraEnv is null) return;
        foreach (var (k, v) in extraEnv)
        {
            if (v is null) psi.Environment.Remove(k);
            else psi.Environment[k] = v;
        }
    }
}
