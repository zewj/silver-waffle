using System;
using System.ComponentModel;
using MultiRobloxManager.Win32;

namespace MultiRobloxManager.Services;

/// <summary>
/// Holds the named mutex Roblox checks at startup to detect "already running".
/// While our handle is open, additional Roblox clients skip the singleton
/// exit path, which is what lets multiple instances run.
/// </summary>
internal sealed class SingletonMutex : IDisposable
{
    private const string MutexName = "ROBLOX_singletonEvent";
    private IntPtr _handle = IntPtr.Zero;

    public void Acquire()
    {
        if (_handle != IntPtr.Zero) return;
        var h = NativeMethods.CreateMutexW(IntPtr.Zero, false, MutexName);
        if (h == IntPtr.Zero)
        {
            throw new Win32Exception(System.Runtime.InteropServices.Marshal.GetLastWin32Error(),
                $"CreateMutexW({MutexName}) failed");
        }
        _handle = h;
        Log.Info("singleton mutex acquired: {0}", MutexName);
    }

    public void Release()
    {
        if (_handle != IntPtr.Zero)
        {
            NativeMethods.CloseHandle(_handle);
            _handle = IntPtr.Zero;
            Log.Info("singleton mutex released");
        }
    }

    public void Dispose() => Release();
}
