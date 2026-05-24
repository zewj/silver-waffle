using System;
using System.IO;
using System.Threading;

namespace MultiRobloxManager.Services;

/// <summary>
/// Tiny rolling file logger. Stdlib only — no Serilog / NLog dep needed
/// for what amounts to "append a line, rotate at 2 MB".
/// </summary>
internal static class Log
{
    private const long MaxBytes = 2_000_000;
    private const int Backups = 5;

    private static readonly object _gate = new();
    private static string? _path;

    public static string Path => _path ??= Setup();

    public static string Setup()
    {
        _path ??= AppPaths.LogFile;
        return _path;
    }

    public static void Info(string fmt, params object?[] args)  => Write("INFO", fmt, args, null);
    public static void Warn(string fmt, params object?[] args)  => Write("WARN", fmt, args, null);
    public static void Error(string fmt, params object?[] args) => Write("ERROR", fmt, args, null);
    public static void Exception(Exception ex, string fmt, params object?[] args) =>
        Write("ERROR", fmt, args, ex);

    private static void Write(string level, string fmt, object?[] args, Exception? ex)
    {
        string line;
        try
        {
            var msg = args.Length == 0 ? fmt : string.Format(fmt, args);
            var stamp = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
            line = $"{stamp} [{level}] {msg}";
            if (ex is not null)
            {
                line += Environment.NewLine + ex;
            }
        }
        catch
        {
            line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} [{level}] <log format error: {fmt}>";
        }

        try
        {
            lock (_gate)
            {
                Rotate();
                File.AppendAllText(Path, line + Environment.NewLine);
            }
        }
        catch { /* logging must never throw */ }
    }

    private static void Rotate()
    {
        try
        {
            var fi = new FileInfo(Path);
            if (!fi.Exists || fi.Length < MaxBytes) return;
            // shift .N -> .N+1, drop the oldest
            for (int i = Backups - 1; i >= 1; i--)
            {
                var src = $"{Path}.{i}";
                var dst = $"{Path}.{i + 1}";
                if (File.Exists(dst)) File.Delete(dst);
                if (File.Exists(src)) File.Move(src, dst);
            }
            var first = $"{Path}.1";
            if (File.Exists(first)) File.Delete(first);
            File.Move(Path, first);
        }
        catch { /* swallow — losing rotation is better than losing the app */ }
    }
}
