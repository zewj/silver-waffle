using System;
using System.Windows;
using System.Windows.Threading;
using MultiRobloxManager.Services;

namespace MultiRobloxManager;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        Log.Setup();
        Log.Info("Multi Roblox Manager (C#) starting; log file at {0}", Log.Path);

        // Capture exceptions that would otherwise silently kill the process.
        DispatcherUnhandledException += (s, ev) =>
        {
            Log.Exception(ev.Exception, "Unhandled UI exception");
            MessageBox.Show($"{ev.Exception.Message}\n\nSee {Log.Path}",
                "Multi Roblox Manager",
                MessageBoxButton.OK, MessageBoxImage.Error);
            ev.Handled = true;
        };
        AppDomain.CurrentDomain.UnhandledException += (s, ev) =>
        {
            Log.Exception(ev.ExceptionObject as Exception ?? new Exception("non-Exception"),
                "Unhandled domain exception");
        };

        base.OnStartup(e);
    }
}
