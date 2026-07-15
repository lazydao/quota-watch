using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using Forms = System.Windows.Forms;

namespace QuotaWatch.Tray;

public partial class MainWindow : Window
{
    private const int PopupMargin = 12;
    private int _visibilityGeneration;

    public MainWindow()
    {
        InitializeComponent();
        Deactivated += OnDeactivated;
    }

    public event EventHandler? RefreshRequested;

    public void ToggleNearTray()
    {
        if (IsVisible)
        {
            HidePopup();
        }
        else
        {
            ShowNearTray();
        }
    }

    public void ShowNearTray()
    {
        _visibilityGeneration++;
        Show();
        UpdateLayout();
        PositionNearTray();
        Activate();
        RootPanel.Focus();
    }

    public void HidePopup()
    {
        _visibilityGeneration++;
        Hide();
    }

    private async void OnDeactivated(object? sender, EventArgs e)
    {
        var generation = _visibilityGeneration;
        await Task.Delay(140);
        if (generation == _visibilityGeneration && IsVisible && !IsActive)
        {
            HidePopup();
        }
    }

    private void PositionNearTray()
    {
        var cursor = Forms.Control.MousePosition;
        var screen = Forms.Screen.FromPoint(cursor);
        var workArea = screen.WorkingArea;
        var bounds = screen.Bounds;
        var handle = new WindowInteropHelper(this).Handle;
        if (!GetWindowRect(handle, out var windowRect))
        {
            return;
        }

        var width = windowRect.Right - windowRect.Left;
        var height = windowRect.Bottom - windowRect.Top;
        var topGap = workArea.Top - bounds.Top;
        var leftGap = workArea.Left - bounds.Left;
        var rightGap = bounds.Right - workArea.Right;
        var bottomGap = bounds.Bottom - workArea.Bottom;
        var largestGap = Math.Max(Math.Max(topGap, leftGap), Math.Max(rightGap, bottomGap));

        int x;
        int y;
        if (largestGap == topGap && topGap > 0)
        {
            x = cursor.X - width + 24;
            y = workArea.Top + PopupMargin;
        }
        else if (largestGap == leftGap && leftGap > 0)
        {
            x = workArea.Left + PopupMargin;
            y = cursor.Y - height + 24;
        }
        else if (largestGap == rightGap && rightGap > 0)
        {
            x = workArea.Right - width - PopupMargin;
            y = cursor.Y - height + 24;
        }
        else
        {
            x = cursor.X - width + 24;
            y = workArea.Bottom - height - PopupMargin;
        }

        x = Math.Clamp(x, workArea.Left + PopupMargin, workArea.Right - width - PopupMargin);
        y = Math.Clamp(y, workArea.Top + PopupMargin, workArea.Bottom - height - PopupMargin);
        SetWindowPos(handle, new IntPtr(-1), x, y, 0, 0, 0x0001 | 0x0010 | 0x0040);
    }

    private void RefreshButton_OnClick(object sender, RoutedEventArgs e)
    {
        RefreshRequested?.Invoke(this, EventArgs.Empty);
    }

    private void CloseButton_OnClick(object sender, RoutedEventArgs e)
    {
        HidePopup();
    }

    private void Window_OnPreviewKeyDown(object sender, System.Windows.Input.KeyEventArgs e)
    {
        if (e.Key == Key.Escape)
        {
            HidePopup();
            e.Handled = true;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetWindowRect(IntPtr handle, out Rect rect);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetWindowPos(
        IntPtr handle,
        IntPtr insertAfter,
        int x,
        int y,
        int width,
        int height,
        uint flags);
}
