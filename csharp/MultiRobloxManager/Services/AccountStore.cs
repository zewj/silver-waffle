using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using MultiRobloxManager.Models;

namespace MultiRobloxManager.Services;

internal sealed class AccountStore
{
    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    private readonly object _gate = new();
    public List<Account> Accounts { get; private set; } = new();

    public AccountStore() => Load();

    public Account? Find(long userId) => Accounts.FirstOrDefault(a => a.UserId == userId);

    private void Load()
    {
        var path = AppPaths.AccountsFile;
        if (!File.Exists(path)) return;
        try
        {
            var json = File.ReadAllText(path);
            using var doc = JsonDocument.Parse(json);
            if (!doc.RootElement.TryGetProperty("accounts", out var arr)) return;
            var loaded = new List<Account>();
            foreach (var el in arr.EnumerateArray())
            {
                try
                {
                    loaded.Add(JsonSerializer.Deserialize<Account>(el.GetRawText(), JsonOpts)!);
                }
                catch (Exception ex)
                {
                    Log.Exception(ex, "skipping malformed account row");
                }
            }
            Accounts = loaded;
        }
        catch (Exception ex)
        {
            Log.Exception(ex, "could not load account store at {0}", path);
        }
    }

    private void Save()
    {
        lock (_gate)
        {
            try
            {
                var path = AppPaths.AccountsFile;
                var payload = new { accounts = Accounts };
                var tmp = path + ".tmp";
                File.WriteAllText(tmp, JsonSerializer.Serialize(payload, JsonOpts));
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);
            }
            catch (Exception ex)
            {
                Log.Exception(ex, "could not save accounts");
            }
        }
    }

    /// <summary>Validate the cookie with Roblox, then persist (replacing existing).</summary>
    public async Task<Account> AddOrUpdateAsync(string cookie, string nickname = "", string proxy = "")
    {
        var normalizedProxy = Auth.ValidateProxyUrl(proxy);
        var info = await Auth.WhoAmIAsync(cookie, string.IsNullOrEmpty(normalizedProxy) ? null : normalizedProxy);
        var acc = new Account
        {
            UserId = info.Id,
            Username = info.Name,
            DisplayName = info.DisplayName,
            Nickname = nickname,
            CookieBlob = Dpapi.ProtectString(cookie),
            Proxy = normalizedProxy,
        };
        lock (_gate)
        {
            Accounts.RemoveAll(a => a.UserId == acc.UserId);
            Accounts.Add(acc);
        }
        Save();
        Log.Info("saved account {0} (user_id={1}, proxy={2})", acc.Label, acc.UserId, !string.IsNullOrEmpty(normalizedProxy));
        return acc;
    }

    public void UpdateProxy(long userId, string proxy)
    {
        var normalized = Auth.ValidateProxyUrl(proxy);
        lock (_gate)
        {
            var acc = Accounts.FirstOrDefault(a => a.UserId == userId);
            if (acc is null) return;
            acc.Proxy = normalized;
        }
        Save();
    }

    public void Remove(long userId)
    {
        lock (_gate) { Accounts.RemoveAll(a => a.UserId == userId); }
        Save();
    }
}
