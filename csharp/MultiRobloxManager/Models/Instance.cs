using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace MultiRobloxManager.Models;

public enum InstanceStatus { Starting, Running, Crashed, Closed }

/// <summary>
/// A running Roblox client tracked by the manager. Implements
/// INotifyPropertyChanged so the WPF DataGrid auto-refreshes when a field
/// (e.g. status, last-known sample) changes from a background thread.
/// </summary>
public sealed class Instance : INotifyPropertyChanged
{
    public string Label { get; }
    public long PlaceId { get; }
    public Account? Account { get; }

    private int? _pid;
    public int? Pid
    {
        get => _pid;
        set { _pid = value; Notify(); Notify(nameof(PidDisplay)); }
    }

    public string PidDisplay => _pid?.ToString() ?? "—";

    private IntPtr _hwnd;
    public IntPtr Hwnd
    {
        get => _hwnd;
        set { _hwnd = value; Notify(); }
    }

    private string? _jobId;
    public string? JobId
    {
        get => _jobId;
        set { _jobId = value; Notify(); Notify(nameof(JobIdDisplay)); }
    }

    public string JobIdDisplay => string.IsNullOrEmpty(_jobId) ? "—" : _jobId;

    private InstanceStatus _status = InstanceStatus.Starting;
    public InstanceStatus Status
    {
        get => _status;
        set { _status = value; Notify(); Notify(nameof(StatusDisplay)); Notify(nameof(IsCrashed)); }
    }

    public string StatusDisplay => _status switch
    {
        InstanceStatus.Starting => "◌ starting",
        InstanceStatus.Running  => "● running",
        InstanceStatus.Crashed  => "✕ crashed",
        InstanceStatus.Closed   => "■ closed",
        _ => _status.ToString(),
    };

    public bool IsCrashed => _status == InstanceStatus.Crashed;

    public string AccountLabel => Account?.Label ?? "(signed-in)";

    private double _cpuPercent;
    public double CpuPercent
    {
        get => _cpuPercent;
        set { _cpuPercent = value; Notify(); Notify(nameof(CpuDisplay)); }
    }

    public string CpuDisplay => Status == InstanceStatus.Running ? $"{_cpuPercent:F0}" : "—";

    private double _ramMb;
    public double RamMb
    {
        get => _ramMb;
        set { _ramMb = value; Notify(); Notify(nameof(RamDisplay)); }
    }

    public string RamDisplay => Status == InstanceStatus.Running ? $"{_ramMb:F0}" : "—";

    /// <summary>Recently-used job IDs, so server hop doesn't re-pick the same one.</summary>
    internal LinkedList<string> RecentJobs { get; } = new();

    public Instance(string label, long placeId, Account? account)
    {
        Label = label;
        PlaceId = placeId;
        Account = account;
    }

    public void RememberJob(string jobId, int cap = 10)
    {
        if (string.IsNullOrEmpty(jobId)) return;
        JobId = jobId;
        RecentJobs.Remove(jobId);
        RecentJobs.AddLast(jobId);
        while (RecentJobs.Count > cap) RecentJobs.RemoveFirst();
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Notify([CallerMemberName] string? name = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
