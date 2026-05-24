using System;
using System.IO;
using System.Text.Json;
using MultiRobloxManager.Models;

namespace MultiRobloxManager.Services;

internal sealed class ConfigStore
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    public Config Cfg { get; private set; } = new();
    private readonly object _gate = new();

    public ConfigStore() => Load();

    private void Load()
    {
        var path = AppPaths.ConfigFile;
        if (!File.Exists(path))
        {
            Cfg = new Config();
            return;
        }
        try
        {
            var json = File.ReadAllText(path);
            Cfg = JsonSerializer.Deserialize<Config>(json, JsonOpts) ?? new Config();
            // Defensive: clamp known string enums.
            if (Cfg.LaunchMode is not ("protocol" or "direct")) Cfg.LaunchMode = "protocol";
            if (Cfg.Theme is not ("dark" or "light")) Cfg.Theme = "dark";
        }
        catch (Exception ex)
        {
            Log.Exception(ex, "could not load config; starting fresh");
            Cfg = new Config();
        }
    }

    public void Save()
    {
        lock (_gate)
        {
            try
            {
                var path = AppPaths.ConfigFile;
                var tmp = path + ".tmp";
                File.WriteAllText(tmp, JsonSerializer.Serialize(Cfg, JsonOpts));
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);
            }
            catch (Exception ex)
            {
                Log.Exception(ex, "could not save config");
            }
        }
    }

    public void RememberPlace(long placeId, int cap = 10)
    {
        Cfg.RecentPlaces.Remove(placeId);
        Cfg.RecentPlaces.Insert(0, placeId);
        if (Cfg.RecentPlaces.Count > cap)
            Cfg.RecentPlaces.RemoveRange(cap, Cfg.RecentPlaces.Count - cap);
        Save();
    }

    public void UpsertPreset(Preset preset)
    {
        Cfg.Presets.RemoveAll(p =>
            p.Label == preset.Label &&
            p.PlaceId == preset.PlaceId &&
            p.AccountUserId == preset.AccountUserId);
        Cfg.Presets.Add(preset);
        Save();
    }

    public void RemovePreset(int index)
    {
        if (index >= 0 && index < Cfg.Presets.Count)
        {
            Cfg.Presets.RemoveAt(index);
            Save();
        }
    }
}
