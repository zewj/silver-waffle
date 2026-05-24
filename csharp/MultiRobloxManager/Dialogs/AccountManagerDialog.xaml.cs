using System;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using MultiRobloxManager.Models;
using MultiRobloxManager.Services;
using MultiRobloxManager.Win32;

namespace MultiRobloxManager.Dialogs;

public partial class AccountManagerDialog : Window
{
    private readonly AccountStore _store;

    public AccountManagerDialog(AccountStore store)
    {
        _store = store;
        InitializeComponent();
        Refresh();
        SourceInitialized += (_, _) =>
        {
            var hwnd = new WindowInteropHelper(this).Handle;
            NativeMethods.ApplyDarkTitleBar(hwnd, dark: true);
        };
    }

    private void Refresh()
    {
        AccountsGrid.ItemsSource = null;
        AccountsGrid.ItemsSource = _store.Accounts.ToList();
    }

    private void OnSelectRow(object sender, SelectionChangedEventArgs e)
    {
        if (AccountsGrid.SelectedItem is not Account acc) return;
        NicknameEdit.Text = acc.Nickname;
        ProxyEdit.Text = acc.Proxy;
    }

    private async void OnAdd(object sender, RoutedEventArgs e)
    {
        var cookie = CookieEdit.Text.Trim();
        if (string.IsNullOrEmpty(cookie))
        {
            MessageBox.Show(this, "Paste your .ROBLOSECURITY cookie.", "Missing");
            return;
        }
        AddBtn.IsEnabled = false;
        StatusText.Text = "Validating cookie with Roblox…";
        try
        {
            var acc = await _store.AddOrUpdateAsync(cookie,
                nickname: NicknameEdit.Text.Trim(),
                proxy: ProxyEdit.Text.Trim());
            CookieEdit.Text = "";
            NicknameEdit.Text = "";
            ProxyEdit.Text = "";
            StatusText.Text = $"Saved {acc.Label} (user {acc.UserId}).";
            Refresh();
        }
        catch (Exception ex)
        {
            Log.Exception(ex, "account validation failed");
            MessageBox.Show(this, ex.Message, "Validation failed",
                MessageBoxButton.OK, MessageBoxImage.Error);
            StatusText.Text = $"Error: {ex.Message}";
        }
        finally
        {
            AddBtn.IsEnabled = true;
        }
    }

    private void OnUpdateProxy(object sender, RoutedEventArgs e)
    {
        if (AccountsGrid.SelectedItem is not Account acc)
        {
            MessageBox.Show(this, "Select an account row first.");
            return;
        }
        try
        {
            _store.UpdateProxy(acc.UserId, ProxyEdit.Text.Trim());
            StatusText.Text = "Proxy updated.";
            Refresh();
        }
        catch (Exception ex)
        {
            StatusText.Text = $"Error: {ex.Message}";
        }
    }

    private void OnRemove(object sender, RoutedEventArgs e)
    {
        if (AccountsGrid.SelectedItem is not Account acc) return;
        if (MessageBox.Show(this, $"Remove {acc.Label}?", "Remove",
                MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
            return;
        _store.Remove(acc.UserId);
        StatusText.Text = $"Removed {acc.Label}.";
        Refresh();
    }
}
