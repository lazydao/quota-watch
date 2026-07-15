using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows;
using System.Windows.Threading;
using QuotaWatch.Tray.Services;
using QuotaWatch.Tray.ViewModels;
using Forms = System.Windows.Forms;

namespace QuotaWatch.Tray;

public sealed class TrayApplicationController : IDisposable
{
    private static readonly TimeSpan RefreshInterval = TimeSpan.FromMinutes(5);

    private readonly DashboardViewModel _dashboard = new();
    private readonly MainWindow _window;
    private readonly QuotaCliClient _client = new();
    private readonly SnapshotCache _cache = new();
    private readonly SemaphoreSlim _refreshLock = new(1, 1);
    private readonly DispatcherTimer _refreshTimer;
    private readonly Forms.NotifyIcon _trayIcon;
    private readonly Forms.ToolStripMenuItem _startupMenuItem;
    private Icon? _currentIcon;
    private bool _disposed;

    public TrayApplicationController()
    {
        _window = new MainWindow { DataContext = _dashboard };
        _window.RefreshRequested += async (_, _) => await RefreshAsync();

        _startupMenuItem = new Forms.ToolStripMenuItem("Start with Windows");
        _startupMenuItem.Click += (_, _) => ToggleStartupRegistration();

        var contextMenu = new Forms.ContextMenuStrip();
        contextMenu.Items.Add("Show quotas", null, (_, _) => ShowWindow());
        contextMenu.Items.Add("Refresh now", null, async (_, _) => await RefreshAsync());
        contextMenu.Items.Add(new Forms.ToolStripSeparator());
        contextMenu.Items.Add(_startupMenuItem);
        contextMenu.Items.Add("Open terminal dashboard", null, (_, _) => OpenTerminalDashboard());
        contextMenu.Items.Add(new Forms.ToolStripSeparator());
        contextMenu.Items.Add("Exit", null, (_, _) => ExitApplication());
        contextMenu.Opening += (_, _) => _startupMenuItem.Checked = StartupRegistration.IsEnabled();

        _currentIcon = TrayIconFactory.Create(QuotaSeverity.Loading);
        _trayIcon = new Forms.NotifyIcon
        {
            Icon = _currentIcon,
            Text = "Quota Watch — loading",
            ContextMenuStrip = contextMenu,
            Visible = true,
        };
        _trayIcon.MouseClick += TrayIcon_OnMouseClick;

        _refreshTimer = new DispatcherTimer(DispatcherPriority.Background)
        {
            Interval = RefreshInterval,
        };
        _refreshTimer.Tick += async (_, _) => await RefreshAsync();
    }

    public async Task InitializeAsync()
    {
        var cachedSnapshot = await _cache.LoadAsync();
        if (cachedSnapshot is not null)
        {
            _dashboard.ApplySnapshot(cachedSnapshot, fromCache: true);
            UpdateTrayVisual();
        }

        _refreshTimer.Start();
        _ = RefreshAsync();
    }

    public void ShowWindow()
    {
        System.Windows.Application.Current.Dispatcher.BeginInvoke(_window.ShowNearTray, DispatcherPriority.Normal);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _refreshTimer.Stop();
        _trayIcon.Visible = false;
        _trayIcon.Dispose();
        _currentIcon?.Dispose();
        _window.Close();
        _refreshLock.Dispose();
    }

    private async Task RefreshAsync()
    {
        if (!await _refreshLock.WaitAsync(0))
        {
            return;
        }

        try
        {
            _dashboard.SetRefreshing(true);
            var snapshot = await _client.FetchAsync();
            _dashboard.ApplySnapshot(snapshot);
            UpdateTrayVisual();

            try
            {
                await _cache.SaveAsync(snapshot);
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
        catch (Exception error) when (error is InvalidOperationException or TimeoutException or Win32Exception)
        {
            _dashboard.SetRefreshError(error.Message);
            UpdateTrayVisual();
        }
        finally
        {
            _dashboard.SetRefreshing(false);
            _refreshLock.Release();
        }
    }

    private void TrayIcon_OnMouseClick(object? sender, Forms.MouseEventArgs e)
    {
        if (e.Button != Forms.MouseButtons.Left)
        {
            return;
        }

        System.Windows.Application.Current.Dispatcher.BeginInvoke(_window.ToggleNearTray, DispatcherPriority.Normal);
    }

    private void UpdateTrayVisual()
    {
        var replacement = TrayIconFactory.Create(_dashboard.Severity);
        var previous = _currentIcon;
        _currentIcon = replacement;
        _trayIcon.Icon = replacement;
        _trayIcon.Text = ClampTooltip(_dashboard.TrayTooltip);
        previous?.Dispose();
    }

    private void ToggleStartupRegistration()
    {
        try
        {
            var enable = !StartupRegistration.IsEnabled();
            StartupRegistration.SetEnabled(enable);
            _startupMenuItem.Checked = enable;
        }
        catch (Exception error) when (error is InvalidOperationException or UnauthorizedAccessException)
        {
            _trayIcon.BalloonTipTitle = "Quota Watch";
            _trayIcon.BalloonTipText = $"Unable to update startup settings: {error.Message}";
            _trayIcon.ShowBalloonTip(4000);
        }
    }

    private void OpenTerminalDashboard()
    {
        if (!TryStartTerminal("pwsh.exe"))
        {
            TryStartTerminal("powershell.exe");
        }
    }

    private static bool TryStartTerminal(string executable)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = executable,
                Arguments = "-NoExit -Command quota",
                UseShellExecute = true,
            });
            return true;
        }
        catch (Win32Exception)
        {
            return false;
        }
    }

    private void ExitApplication()
    {
        Dispose();
        System.Windows.Application.Current.Shutdown();
    }

    private static string ClampTooltip(string tooltip)
    {
        const int maxLength = 63;
        return tooltip.Length <= maxLength ? tooltip : tooltip[..maxLength];
    }
}
