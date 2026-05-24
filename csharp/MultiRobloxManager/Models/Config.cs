using System.Collections.Generic;

namespace MultiRobloxManager.Models;

public sealed class Config
{
    public List<Preset> Presets { get; set; } = new();
    public List<long> RecentPlaces { get; set; } = new();

    /// <summary>Seconds to space launches by, to avoid Roblox join rate limits.</summary>
    public double LaunchCooldown { get; set; } = 2.5;

    /// <summary>"protocol" or "direct".</summary>
    public string LaunchMode { get; set; } = "protocol";

    /// <summary>"dark" or "light".</summary>
    public string Theme { get; set; } = "dark";

    public string WebhookUrl { get; set; } = "";
    public bool WebhookOnCrash { get; set; } = true;

    public string BotToken { get; set; } = "";
    public bool BotEnabled { get; set; } = false;
    public List<string> BotUserIds { get; set; } = new();
}
