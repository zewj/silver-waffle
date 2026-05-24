using System.Text.Json.Serialization;

namespace MultiRobloxManager.Models;

/// <summary>One Roblox account record. Cookie is DPAPI-encrypted on disk.</summary>
public sealed class Account
{
    public long UserId { get; set; }
    public string Username { get; set; } = "";
    public string DisplayName { get; set; } = "";
    public string Nickname { get; set; } = "";

    /// <summary>Base64(DPAPI-encrypted .ROBLOSECURITY).</summary>
    [JsonPropertyName("cookie_blob")]
    public string CookieBlob { get; set; } = "";

    /// <summary>Optional per-account HTTP/SOCKS proxy URL.</summary>
    public string Proxy { get; set; } = "";

    public string Label => !string.IsNullOrWhiteSpace(Nickname)
        ? Nickname
        : !string.IsNullOrWhiteSpace(DisplayName)
            ? DisplayName
            : Username;

    public string ResolveCookie()
        => Services.Dpapi.UnprotectString(CookieBlob);

    public string? ProxyOrNull()
        => string.IsNullOrWhiteSpace(Proxy) ? null : Proxy;
}
