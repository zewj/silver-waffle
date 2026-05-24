using System;
using System.IO;

namespace MultiRobloxManager.Services;

/// <summary>Where the app stores its config / data / logs.</summary>
internal static class AppPaths
{
    public const string AppFolderName = "MultiRobloxManager";

    public static string Root
    {
        get
        {
            var roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            var path = Path.Combine(roaming, AppFolderName);
            Directory.CreateDirectory(path);
            return path;
        }
    }

    public static string Data => EnsureDir(Path.Combine(Root, "data"));
    public static string Logs => EnsureDir(Path.Combine(Root, "logs"));

    public static string AccountsFile => Path.Combine(Root, "accounts.json");
    public static string ConfigFile => Path.Combine(Root, "config.json");
    public static string LogFile => Path.Combine(Logs, "manager.log");

    public static string LocalAppData =>
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

    private static string EnsureDir(string path)
    {
        Directory.CreateDirectory(path);
        return path;
    }
}
