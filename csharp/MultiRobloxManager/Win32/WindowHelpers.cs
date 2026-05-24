using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Text;
using System.Threading;

namespace MultiRobloxManager.Win32;

internal static class WindowHelpers
{
    public const string RobloxExe = "RobloxPlayerBeta.exe";

    /// <summary>Live PIDs of all RobloxPlayerBeta.exe processes.</summary>
    public static HashSet<int> ListRobloxPids()
    {
        var pids = new HashSet<int>();
        // Process.GetProcessesByName matches by name without the .exe suffix.
        foreach (var p in Process.GetProcessesByName("RobloxPlayerBeta"))
        {
            try { pids.Add(p.Id); }
            catch { /* process exited between enumeration and access */ }
            finally { p.Dispose(); }
        }
        return pids;
    }

    /// <summary>Block until a top-level "Roblox" window owned by pid appears, or timeout.</summary>
    public static IntPtr FindWindowForPid(int pid, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            var hwnd = FindHwnd(pid);
            if (hwnd != IntPtr.Zero) return hwnd;
            Thread.Sleep(400);
        }
        return IntPtr.Zero;
    }

    private static IntPtr FindHwnd(int targetPid)
    {
        IntPtr found = IntPtr.Zero;
        bool Callback(IntPtr hwnd, IntPtr lParam)
        {
            if (!NativeMethods.IsWindowVisible(hwnd)) return true;
            NativeMethods.GetWindowThreadProcessId(hwnd, out uint pid);
            if ((int)pid != targetPid) return true;
            var sb = new StringBuilder(256);
            NativeMethods.GetWindowTextW(hwnd, sb, sb.Capacity);
            var title = sb.ToString();
            if (!string.IsNullOrEmpty(title) && title.Contains("Roblox", StringComparison.Ordinal))
            {
                found = hwnd;
                return false;
            }
            return true;
        }
        NativeMethods.EnumWindows(Callback, IntPtr.Zero);
        return found;
    }

    /// <summary>
    /// Bring a window to the foreground reliably across focus-stealing checks.
    /// Uses AttachThreadInput to bypass the foreground lockout that bites
    /// SetForegroundWindow on Windows 7+.
    /// </summary>
    public static bool FocusWindow(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) return false;
        NativeMethods.ShowWindow(hwnd, NativeMethods.SW_RESTORE);
        var fg = NativeMethods.GetForegroundWindow();
        var fgThread = NativeMethods.GetWindowThreadProcessId(fg, out _);
        var curThread = NativeMethods.GetCurrentThreadId();
        bool attached = false;
        if (fgThread != 0 && fgThread != curThread)
        {
            attached = NativeMethods.AttachThreadInput(curThread, fgThread, true);
        }
        try
        {
            NativeMethods.BringWindowToTop(hwnd);
            return NativeMethods.SetForegroundWindow(hwnd);
        }
        finally
        {
            if (attached)
                NativeMethods.AttachThreadInput(curThread, fgThread, false);
        }
    }

    public static void KillPid(int pid)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            p.Kill(entireProcessTree: true);
            p.WaitForExit(2000);
        }
        catch (ArgumentException) { /* already gone */ }
        catch (System.ComponentModel.Win32Exception) { /* access denied or gone */ }
        catch (InvalidOperationException) { /* already exited */ }
    }
}
