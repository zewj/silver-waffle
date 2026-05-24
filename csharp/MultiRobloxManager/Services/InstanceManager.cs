using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MultiRobloxManager.Models;
using MultiRobloxManager.Win32;

namespace MultiRobloxManager.Services;

/// <summary>
/// Orchestrator. Holds the singleton mutex, launches Roblox clients with
/// per-account auth tickets, hops servers, tracks PIDs / HWNDs.
///
/// MVP scope: launch (protocol + direct), focus, close, server hop. The
/// Anti-AFK loop, per-account profile isolation, screenshot, Discord bot,
/// and webhook live in their own files (some stubbed for follow-up sessions).
/// </summary>
internal sealed class InstanceManager
{
    public const double LaunchCooldownDefault = 2.5;
    public const double ProtocolPidTimeoutSec = 90.0;
    public const double DirectPidTimeoutSec = 30.0;
    public const double HopOverlapTimeoutSec = 35.0;

    private readonly SingletonMutex _mutex = new();
    private readonly object _lock = new();
    private DateTime _lastLaunch = DateTime.MinValue;

    public double LaunchCooldown { get; set; } = LaunchCooldownDefault;
    /// <summary>"protocol" (default, via launcher) or "direct".</summary>
    public string LaunchMode { get; set; } = "protocol";

    /// <summary>Observable so the WPF DataGrid auto-updates as instances appear/disappear.</summary>
    public ObservableCollection<Instance> Instances { get; } = new();

    /// <summary>Fires exactly once on the running -> crashed transition.</summary>
    public event Action<Instance>? InstanceCrashed;

    public void Start() => _mutex.Acquire();

    public void Shutdown()
    {
        // Fired during window close — KillPid can take up to ~2s per process
        // because of WaitForExit. We accept the brief block at shutdown
        // since the window is going away regardless.
        lock (_lock)
        {
            foreach (var inst in Instances.ToList())
            {
                if (inst.Pid is int pid) WindowHelpers.KillPid(pid);
            }
            Instances.Clear();
        }
        _mutex.Release();
    }

    private async Task RespectCooldownAsync()
    {
        var wait = TimeSpan.FromSeconds(LaunchCooldown) - (DateTime.UtcNow - _lastLaunch);
        if (wait > TimeSpan.Zero) await Task.Delay(wait);
        _lastLaunch = DateTime.UtcNow;
    }

    private static async Task<int?> WaitForNewPidAsync(HashSet<int> before, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            var diff = WindowHelpers.ListRobloxPids().Except(before).ToList();
            if (diff.Count > 0) return diff.Max();
            await Task.Delay(400);
        }
        return null;
    }

    /// <summary>Spawn a client, return its resolved PID (or null if it didn't appear in time).</summary>
    private async Task<int?> LaunchProcessAsync(Account? account, long placeId, string? jobId)
    {
        await RespectCooldownAsync();
        Log.Info("launch start place={0} job={1} account={2} mode={3}",
            placeId, jobId ?? "(any)", account?.Label ?? "(none)", LaunchMode);

        if (account is null)
        {
            // No saved account — protocol handoff with whoever is signed in.
            var before = WindowHelpers.ListRobloxPids();
            var uri = jobId is null ? Servers.LaunchUri(placeId) : Servers.JoinUri(placeId, jobId);
            Process.Start(new ProcessStartInfo { FileName = uri, UseShellExecute = true });
            return await WaitForNewPidAsync(before, TimeSpan.FromSeconds(ProtocolPidTimeoutSec));
        }

        if (LaunchMode == "direct")
        {
            var before = WindowHelpers.ListRobloxPids();
            var ticket = await Auth.FetchAuthTicketAsync(account.ResolveCookie(), account.ProxyOrNull());
            Launcher.LaunchDirectWithTicket(ticket, placeId, jobId);
            return await WaitForNewPidAsync(before, TimeSpan.FromSeconds(DirectPidTimeoutSec));
        }

        // Protocol-first with direct fallback.
        {
            var before = WindowHelpers.ListRobloxPids();
            var ticket = await Auth.FetchAuthTicketAsync(account.ResolveCookie(), account.ProxyOrNull());
            Launcher.LaunchProtocolWithTicket(ticket, placeId, jobId);
            var pid = await WaitForNewPidAsync(before, TimeSpan.FromSeconds(ProtocolPidTimeoutSec));
            if (pid is not null) return pid;
            Log.Warn("protocol launch stalled for {0}; falling back to direct", account.Label);
            var before2 = WindowHelpers.ListRobloxPids();
            var ticket2 = await Auth.FetchAuthTicketAsync(account.ResolveCookie(), account.ProxyOrNull());
            Launcher.LaunchDirectWithTicket(ticket2, placeId, jobId);
            return await WaitForNewPidAsync(before2, TimeSpan.FromSeconds(DirectPidTimeoutSec));
        }
    }

    /// <summary>Add an instance, kick off the launch, return the (incomplete) Instance immediately.</summary>
    public Instance AddInstance(string label, long placeId, Account? account, string? jobId = null)
    {
        var inst = new Instance(label, placeId, account);
        Instances.Add(inst);
        _ = LaunchInstanceAsync(inst, jobId);
        return inst;
    }

    private async Task LaunchInstanceAsync(Instance inst, string? jobId)
    {
        try
        {
            var pid = await LaunchProcessAsync(inst.Account, inst.PlaceId, jobId);
            inst.Pid = pid;
            inst.Status = pid is null ? InstanceStatus.Crashed : InstanceStatus.Running;
            if (pid is int p)
            {
                // FindWindowForPid is a blocking poll (Thread.Sleep in a loop
                // up to 30s). Run on the thread pool so the UI thread stays
                // responsive; the property assignment back on the UI thread
                // is what fires the binding update.
                inst.Hwnd = await Task.Run(
                    () => WindowHelpers.FindWindowForPid(p, TimeSpan.FromSeconds(30)));
            }
            if (jobId is not null) inst.RememberJob(jobId);
            Log.Info("instance {0} ready pid={1} hwnd={2}", inst.Label, inst.Pid, inst.Hwnd);
        }
        catch (Exception ex)
        {
            Log.Exception(ex, "launch failed for {0}", inst.Label);
            inst.Status = InstanceStatus.Crashed;
            throw;
        }
    }

    public bool Focus(Instance inst)
    {
        if (inst.Hwnd == IntPtr.Zero && inst.Pid is int pid)
        {
            // 2s blocking poll — acceptable from a button click.
            inst.Hwnd = WindowHelpers.FindWindowForPid(pid, TimeSpan.FromSeconds(2));
        }
        return inst.Hwnd != IntPtr.Zero && WindowHelpers.FocusWindow(inst.Hwnd);
    }

    public void Close(Instance inst)
    {
        // Pull from the visible collection first so the UI updates instantly;
        // the actual kill (which can block briefly via WaitForExit) goes to
        // the thread pool so the click handler returns immediately.
        lock (_lock)
        {
            if (Instances.Contains(inst)) Instances.Remove(inst);
        }
        if (inst.Pid is int pid)
        {
            Task.Run(() => WindowHelpers.KillPid(pid));
        }
        Log.Info("closed instance {0} (pid={1})", inst.Label, inst.Pid);
    }

    /// <summary>Overlap-style server hop: bring the new client up before killing the old.</summary>
    public async Task<string?> ServerHopAsync(Instance inst)
    {
        var proxy = inst.Account?.ProxyOrNull();
        var srv = await Servers.PickServerAsync(inst.PlaceId, inst.RecentJobs, proxy);
        if (srv is null)
        {
            Log.Info("hop: no eligible server for place {0}", inst.PlaceId);
            return null;
        }
        var newJob = srv.Id;
        var oldPid = inst.Pid;
        int? newPid;
        try
        {
            newPid = await LaunchProcessAsync(inst.Account, inst.PlaceId, newJob);
        }
        catch (Exception ex)
        {
            Log.Exception(ex, "hop launch failed for {0}", inst.Label);
            return null;
        }
        IntPtr newHwnd = IntPtr.Zero;
        if (newPid is int p)
        {
            // Blocking poll — run on the thread pool so we don't freeze the
            // UI during the up-to-35s overlap window.
            newHwnd = await Task.Run(
                () => WindowHelpers.FindWindowForPid(p, TimeSpan.FromSeconds(HopOverlapTimeoutSec)));
        }
        if (oldPid is int op) await Task.Run(() => WindowHelpers.KillPid(op));
        inst.Pid = newPid;
        inst.Hwnd = newHwnd;
        inst.RememberJob(newJob);
        inst.Status = newPid is null ? InstanceStatus.Crashed : InstanceStatus.Running;
        Log.Info("hop done {0} -> {1} (pid={2})", inst.Label, newJob, newPid);
        return newJob;
    }

    /// <summary>Sample CPU/RAM for each live instance; flip status on death.</summary>
    public void RefreshStats()
    {
        foreach (var inst in Instances.ToList())
        {
            if (inst.Pid is not int pid) continue;
            try
            {
                using var p = Process.GetProcessById(pid);
                inst.RamMb = p.WorkingSet64 / (1024.0 * 1024.0);
                // Lightweight CPU%: TotalProcessorTime delta over interval. For
                // an MVP, just report 0 — accurate CPU% needs sampling state
                // we can wire in a future polish session.
            }
            catch (ArgumentException)
            {
                if (inst.Status == InstanceStatus.Running)
                {
                    Log.Warn("instance {0} (pid={1}) is no longer alive", inst.Label, pid);
                    inst.Status = InstanceStatus.Crashed;
                    InstanceCrashed?.Invoke(inst);
                }
            }
            catch (Exception ex)
            {
                Log.Exception(ex, "stats sample failed for {0}", inst.Label);
            }
        }
    }
}
