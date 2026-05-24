using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace MultiRobloxManager.Services;

internal static class Servers
{
    private static readonly Regex PlaceUrlRe = new(@"/games/(\d+)", RegexOptions.Compiled);
    private static readonly HttpClient SharedClient = BuildSharedClient();

    private static HttpClient BuildSharedClient()
    {
        var c = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        c.DefaultRequestHeaders.UserAgent.ParseAdd("Roblox/WinInet");
        c.DefaultRequestHeaders.Accept.ParseAdd("application/json");
        return c;
    }

    public static long? ParsePlaceId(string value)
    {
        value = (value ?? "").Trim();
        if (string.IsNullOrEmpty(value)) return null;
        if (long.TryParse(value, out var n)) return n;
        var m = PlaceUrlRe.Match(value);
        return m.Success ? long.Parse(m.Groups[1].Value) : null;
    }

    /// <summary>Build a roblox:// URI for the protocol-handler join.</summary>
    public static string JoinUri(long placeId, string jobId)
        => $"roblox://placeId={placeId}&gameInstanceId={jobId}";

    public static string LaunchUri(long placeId)
        => $"roblox://placeId={placeId}";

    public sealed record ServerInfo(string Id, int Playing, int MaxPlayers);

    public static async Task<List<ServerInfo>> FetchServersAsync(
        long placeId, int limit = 100, string? proxy = null)
    {
        var url = $"https://games.roblox.com/v1/games/{placeId}/servers/Public?sortOrder=Asc&limit={limit}";
        HttpResponseMessage resp;
        if (!string.IsNullOrEmpty(proxy))
        {
            var handler = new HttpClientHandler
            {
                Proxy = new WebProxy(proxy),
                UseProxy = true,
            };
            using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(10) };
            client.DefaultRequestHeaders.UserAgent.ParseAdd("Roblox/WinInet");
            resp = await client.GetAsync(url);
        }
        else
        {
            resp = await SharedClient.GetAsync(url);
        }
        resp.EnsureSuccessStatusCode();
        var body = await resp.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(body);
        var list = new List<ServerInfo>();
        if (!doc.RootElement.TryGetProperty("data", out var data)) return list;
        foreach (var s in data.EnumerateArray())
        {
            var id = s.TryGetProperty("id", out var idE) ? idE.GetString() : null;
            if (string.IsNullOrEmpty(id)) continue;
            int playing = s.TryGetProperty("playing", out var pE) ? pE.GetInt32() : 0;
            int max = s.TryGetProperty("maxPlayers", out var mE) ? mE.GetInt32() : 0;
            list.Add(new ServerInfo(id!, playing, max));
        }
        return list;
    }

    /// <summary>Pick a non-full public server not in the exclude set.</summary>
    public static async Task<ServerInfo?> PickServerAsync(
        long placeId, IEnumerable<string>? excludeJobIds = null, string? proxy = null)
    {
        var exclude = new HashSet<string>(excludeJobIds ?? Array.Empty<string>());
        var all = await FetchServersAsync(placeId, proxy: proxy);
        var candidates = all
            .Where(s => !exclude.Contains(s.Id) && s.MaxPlayers > 0 && s.Playing < s.MaxPlayers)
            .Select(s => new { Free = s.MaxPlayers - s.Playing, s.Playing, Server = s })
            .OrderBy(x => x.Playing == 0) // prefer non-empty
            .ThenByDescending(x => x.Free)
            .ToList();
        return candidates.Count > 0 ? candidates[0].Server : null;
    }
}
