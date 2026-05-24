using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Threading;
using MultiRobloxManager.Dialogs;
using MultiRobloxManager.Models;
using MultiRobloxManager.Services;
using MultiRobloxManager.Win32;

namespace MultiRobloxManager;

public partial class MainWindow : Window
{
    private readonly AccountStore _accounts;
    private readonly ConfigStore _config;
    private readonly InstanceManager _manager;
    private readonly DispatcherTimer _statsTimer;
    private readonly ObservableCollection<PresetRow> _presetRows = new();

    public MainWindow()
    {
        InitializeComponent();
        _accounts = new AccountStore();
        _config = new ConfigStore();
        _manager = new InstanceManager
        {
            LaunchCooldown = _config.Cfg.LaunchCooldown,
            LaunchMode = _config.Cfg.LaunchMode,
        };
        _manager.InstanceCrashed += inst =>
            Dispatcher.BeginInvoke(() => SetStatus($"{inst.Label} crashed.", error: true));
        _manager.Start();

        var version = Launcher.DetectVersion() ?? "unknown";
        MetaLabel.Text = $"Roblox {version}";
        SetStatus($"Ready • Roblox {version} • mutex held");

        InstancesGrid.ItemsSource = _manager.Instances;
        PresetsGrid.ItemsSource = _presetRows;

        if (_config.Cfg.LaunchMode == "direct") DirectRadio.IsChecked = true;
        else ProtocolRadio.IsChecked = true;

        RefreshAccountsDropdown();
        RefreshPresets();

        _statsTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _statsTimer.Tick += (_, _) => _manager.RefreshStats();
        _statsTimer.Start();

        // Apply the OS dark title bar once the HWND is realized.
        SourceInitialized += (_, _) =>
        {
            var hwnd = new WindowInteropHelper(this).Handle;
            NativeMethods.ApplyDarkTitleBar(hwnd, dark: _config.Cfg.Theme != "light");
        };
        Closed += (_, _) =>
        {
            _statsTimer.Stop();
            _manager.Shutdown();
        };
    }

    // ---- helpers --------------------------------------------------------

    private void SetStatus(string msg, bool error = false)
    {
        StatusText.Text = msg;
        if (error) Log.Warn("status (error): {0}", msg);
        else Log.Info("status: {0}", msg);
    }

    private void RefreshAccountsDropdown()
    {
        var prev = AccountCombo.SelectedItem as string;
        AccountCombo.Items.Clear();
        AccountCombo.Items.Add("(launcher's signed-in account)");
        foreach (var a in _accounts.Accounts) AccountCombo.Items.Add(a.Label);
        AccountCombo.SelectedIndex = AccountCombo.Items
            .Cast<string>().ToList().IndexOf(prev ?? "") is int idx && idx >= 0 ? idx : 0;
    }

    private Account? ResolveSelectedAccount()
    {
        var label = AccountCombo.SelectedItem as string;
        if (string.IsNullOrEmpty(label) || label.StartsWith("(")) return null;
        return _accounts.Accounts.FirstOrDefault(a => a.Label == label);
    }

    private void RefreshPresets()
    {
        _presetRows.Clear();
        foreach (var p in _config.Cfg.Presets)
        {
            var acc = p.AccountUserId is long uid ? _accounts.Find(uid) : null;
            var accLabel = acc?.Label
                ?? (p.AccountUserId is null ? "(signed-in)" : "[missing account]");
            _presetRows.Add(new PresetRow(p, accLabel));
        }
    }

    private List<Instance> SelectedInstances()
        => InstancesGrid.SelectedItems.Cast<Instance>().ToList();

    private async Task RunAsync(string status, Func<Task> action)
    {
        SetStatus(status);
        try { await action(); }
        catch (Exception ex)
        {
            Log.Exception(ex, "background action failed");
            SetStatus($"Error: {ex.Message}", error: true);
            MessageBox.Show(this, ex.Message, "Error",
                MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    // ---- menu actions ---------------------------------------------------

    private void OnOpenLogs(object sender, RoutedEventArgs e)
    {
        try { Process.Start(new ProcessStartInfo
        {
            FileName = AppPaths.Logs, UseShellExecute = true,
        }); }
        catch (Exception ex) { MessageBox.Show(this, ex.Message); }
    }

    private void OnQuit(object sender, RoutedEventArgs e) => Close();

    private void OnAbout(object sender, RoutedEventArgs e)
    {
        var v = Launcher.DetectVersion() ?? "unknown";
        MessageBox.Show(this,
            $"Multi Roblox Manager (C# / WPF)\n\nRoblox client: {v}\nLog folder: {AppPaths.Logs}",
            "About", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    private void OnManageAccounts(object sender, RoutedEventArgs e)
    {
        var dlg = new AccountManagerDialog(_accounts) { Owner = this };
        dlg.ShowDialog();
        RefreshAccountsDropdown();
        RefreshPresets();
    }

    // ---- launch ---------------------------------------------------------

    private void OnPlaceKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter) OnLaunch(sender, e);
    }

    private async void OnLaunch(object sender, RoutedEventArgs e)
    {
        var placeId = Servers.ParsePlaceId(PlaceEdit.Text);
        if (placeId is null)
        {
            MessageBox.Show(this, "Enter a numeric placeId or a roblox.com /games/<id>/ URL.",
                "Invalid", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        var account = ResolveSelectedAccount();
        var label = LabelEdit.Text.Trim();
        if (string.IsNullOrEmpty(label))
            label = account?.Label ?? $"Instance {_manager.Instances.Count + 1}";
        _config.RememberPlace(placeId.Value);

        SetStatus($"Launching {label}…");
        var inst = _manager.AddInstance(label, placeId.Value, account);
        // AddInstance kicks off the launch asynchronously; the periodic
        // stats refresh updates PID / status / hwnd as the client comes up.
        await Task.Yield();
        SetStatus($"{inst.Label} queued.");
    }

    private void OnSavePreset(object sender, RoutedEventArgs e)
    {
        var placeId = Servers.ParsePlaceId(PlaceEdit.Text);
        if (placeId is null)
        {
            MessageBox.Show(this, "Enter a placeId or URL to save.", "Invalid");
            return;
        }
        var account = ResolveSelectedAccount();
        var label = LabelEdit.Text.Trim();
        if (string.IsNullOrEmpty(label))
            label = account?.Label ?? $"Preset {_config.Cfg.Presets.Count + 1}";
        _config.UpsertPreset(new Preset
        {
            Label = label,
            PlaceId = placeId.Value,
            AccountUserId = account?.UserId,
        });
        RefreshPresets();
        SetStatus($"Saved preset '{label}'.");
    }

    private async void OnLaunchPreset(object sender, RoutedEventArgs e)
        => await LaunchSelectedPresetAsync();

    private async void OnPresetDoubleClick(object sender, MouseButtonEventArgs e)
        => await LaunchSelectedPresetAsync();

    private async Task LaunchSelectedPresetAsync()
    {
        if (PresetsGrid.SelectedItem is not PresetRow row) return;
        var p = row.Preset;
        var account = p.AccountUserId is long uid ? _accounts.Find(uid) : null;
        SetStatus($"Launching {p.Label}…");
        _manager.AddInstance(p.Label, p.PlaceId, account);
        await Task.Yield();
    }

    private async void OnLaunchAllPresets(object sender, RoutedEventArgs e)
    {
        var presets = _config.Cfg.Presets.ToList();
        if (presets.Count == 0) return;
        SetStatus($"Launching {presets.Count} preset(s)…");
        foreach (var p in presets)
        {
            var account = p.AccountUserId is long uid ? _accounts.Find(uid) : null;
            _manager.AddInstance(p.Label, p.PlaceId, account);
            // Stagger so we don't hammer the launch cooldown all at once.
            await Task.Delay(100);
        }
    }

    private void OnRemovePreset(object sender, RoutedEventArgs e)
    {
        if (PresetsGrid.SelectedItem is not PresetRow row) return;
        var idx = _presetRows.IndexOf(row);
        if (idx < 0) return;
        if (MessageBox.Show(this, $"Remove preset '{row.Preset.Label}'?", "Remove",
                MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
            return;
        _config.RemovePreset(idx);
        RefreshPresets();
    }

    // ---- instance actions ----------------------------------------------

    private void OnFocus(object sender, RoutedEventArgs e)
    {
        var inst = SelectedInstances().FirstOrDefault();
        if (inst is null) return;
        var ok = _manager.Focus(inst);
        SetStatus(ok ? $"Focused {inst.Label}" : $"Could not focus {inst.Label}", error: !ok);
    }

    private void OnCycle(object sender, RoutedEventArgs e)
    {
        var alive = _manager.Instances.Where(i => i.Hwnd != IntPtr.Zero).ToList();
        if (alive.Count == 0) return;
        var current = NativeMethods.GetForegroundWindow();
        var idx = alive.FindIndex(i => i.Hwnd == current);
        var target = idx >= 0 ? alive[(idx + 1) % alive.Count] : alive[0];
        if (_manager.Focus(target)) SetStatus($"Focused {target.Label}");
    }

    private async void OnHop(object sender, RoutedEventArgs e)
    {
        var targets = SelectedInstances();
        if (targets.Count == 0) return;
        await RunAsync($"Server-hopping {targets.Count} instance(s)…", async () =>
        {
            var results = new List<string>();
            foreach (var t in targets)
            {
                var job = await _manager.ServerHopAsync(t);
                results.Add($"{t.Label}→{job ?? "(no server)"}");
            }
            SetStatus("Hop done: " + string.Join(", ", results));
        });
    }

    private void OnCloseInstance(object sender, RoutedEventArgs e)
    {
        var targets = SelectedInstances();
        foreach (var t in targets) _manager.Close(t);
        SetStatus($"Closed {targets.Count} instance(s).");
    }

    private void OnModeChanged(object sender, RoutedEventArgs e)
    {
        var mode = (DirectRadio.IsChecked ?? false) ? "direct" : "protocol";
        _manager.LaunchMode = mode;
        _config.Cfg.LaunchMode = mode;
        _config.Save();
        SetStatus(mode == "protocol"
            ? "Launches will go through RobloxPlayerLauncher."
            : "Launches will spawn RobloxPlayerBeta directly.");
    }
}

/// <summary>Display row for the presets DataGrid (resolved account label).</summary>
public sealed class PresetRow
{
    public Preset Preset { get; }
    public string Label => Preset.Label;
    public long PlaceId => Preset.PlaceId;
    public string AccountDisplay { get; }
    public PresetRow(Preset preset, string accountDisplay)
    {
        Preset = preset;
        AccountDisplay = accountDisplay;
    }
}
