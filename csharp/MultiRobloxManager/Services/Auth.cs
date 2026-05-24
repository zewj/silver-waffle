using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading.Tasks;

namespace MultiRobloxManager.Services;

public sealed class AuthException : Exception
{
    public AuthException(string message) : base(message) { }
}

internal static class Auth
{
    private static HttpClient BuildClient(string cookie, string? proxy)
    {
        var handler = new HttpClientHandler
        {
            UseCookies = true,
            CookieContainer = new CookieContainer(),
            AllowAutoRedirect = true,
        };
        handler.CookieContainer.Add(new Cookie(".ROBLOSECURITY", cookie, "/", ".roblox.com"));
        if (!string.IsNullOrWhiteSpace(proxy))
        {
            handler.Proxy = new WebProxy(proxy);
            handler.UseProxy = true;
        }
        var client = new HttpClient(handler, disposeHandler: true)
        {
            Timeout = TimeSpan.FromSeconds(15),
        };
        client.DefaultRequestHeaders.UserAgent.ParseAdd("Roblox/WinInet");
        client.DefaultRequestHeaders.Referrer = new Uri("https://www.roblox.com/");
        client.DefaultRequestHeaders.Add("Origin", "https://www.roblox.com");
        return client;
    }

    /// <summary>POST /v2/logout with no token; Roblox responds 403 + X-CSRF-TOKEN header.</summary>
    private static async Task<string> FetchCsrfTokenAsync(HttpClient client)
    {
        var resp = await client.PostAsync("https://auth.roblox.com/v2/logout", null);
        if (resp.Headers.TryGetValues("x-csrf-token", out var values))
        {
            foreach (var v in values) return v;
        }
        throw new AuthException("Could not retrieve CSRF token (cookie may be invalid).");
    }

    /// <summary>Validate a cookie; returns {id, name, displayName} on success.</summary>
    public static async Task<(long Id, string Name, string DisplayName)> WhoAmIAsync(
        string cookie, string? proxy = null)
    {
        using var client = BuildClient(cookie, proxy);
        var resp = await client.GetAsync("https://users.roblox.com/v1/users/authenticated");
        if ((int)resp.StatusCode == 401)
            throw new AuthException("Cookie rejected (401). Re-export .ROBLOSECURITY from the browser.");
        resp.EnsureSuccessStatusCode();
        var body = await resp.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(body);
        var root = doc.RootElement;
        long id = root.GetProperty("id").GetInt64();
        string name = root.GetProperty("name").GetString() ?? "";
        string dn = root.TryGetProperty("displayName", out var d) ? (d.GetString() ?? name) : name;
        return (id, name, dn);
    }

    /// <summary>Exchange a .ROBLOSECURITY cookie for a one-shot launch ticket.</summary>
    public static async Task<string> FetchAuthTicketAsync(string cookie, string? proxy = null)
    {
        using var client = BuildClient(cookie, proxy);
        var csrf = await FetchCsrfTokenAsync(client);
        var req = new HttpRequestMessage(HttpMethod.Post,
            "https://auth.roblox.com/v1/authentication-ticket/")
        {
            Content = new StringContent("{}", System.Text.Encoding.UTF8, "application/json"),
        };
        req.Headers.Add("X-CSRF-TOKEN", csrf);
        req.Headers.Add("RBXAuthenticationNegotiation", "1");
        var resp = await client.SendAsync(req);
        if ((int)resp.StatusCode is 401 or 403)
            throw new AuthException($"Auth ticket refused ({(int)resp.StatusCode}); cookie expired?");
        resp.EnsureSuccessStatusCode();
        if (resp.Headers.TryGetValues("rbx-authentication-ticket", out var values))
        {
            foreach (var t in values) return t;
        }
        throw new AuthException("No rbx-authentication-ticket header in response.");
    }

    /// <summary>Build the PlaceLauncher URL the Roblox client expects in its -j argument.</summary>
    public static string PlaceLauncherUrl(long placeId, string? jobId = null, long? browserTrackerId = null)
    {
        var parts = new List<(string, string)>
        {
            (jobId is null ? "request" : "request", jobId is null ? "RequestGame" : "RequestGameJob"),
            ("placeId", placeId.ToString()),
            ("isPlayTogetherGame", "false"),
            ("isPartyLeader", "false"),
        };
        if (jobId is not null) parts.Add(("gameId", jobId));
        if (browserTrackerId is not null)
            parts.Add(("browserTrackerId", browserTrackerId.Value.ToString()));
        var query = string.Join("&", parts.ConvertAll(p => $"{p.Item1}={p.Item2}"));
        return $"https://assetgame.roblox.com/game/PlaceLauncher.ashx?{query}";
    }

    public static long LaunchTimeMs() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

    /// <summary>
    /// Validate + normalize a proxy URL. Empty / null = no proxy. Throws
    /// ArgumentException on unsupported schemes. We don't need a special
    /// PySocks-equivalent check here because .NET's WebProxy / HttpClient
    /// supports SOCKS5 natively in .NET 6+.
    /// </summary>
    public static string ValidateProxyUrl(string? url)
    {
        url = (url ?? "").Trim();
        if (url.Length == 0) return "";
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed))
            throw new ArgumentException($"Invalid proxy URL: {url}");
        var scheme = parsed.Scheme.ToLowerInvariant();
        var allowed = new HashSet<string> { "http", "https", "socks5", "socks5h", "socks4", "socks4a" };
        if (!allowed.Contains(scheme))
            throw new ArgumentException(
                $"Unsupported proxy scheme '{scheme}'. Use http(s):// or socks5://.");
        if (string.IsNullOrEmpty(parsed.Host))
            throw new ArgumentException("Proxy URL is missing a host.");
        return url;
    }
}
