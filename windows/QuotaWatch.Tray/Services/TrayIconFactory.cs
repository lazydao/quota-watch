using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;

namespace QuotaWatch.Tray.Services;

public enum QuotaSeverity
{
    Normal,
    Warning,
    Critical,
    Loading,
}

public static class TrayIconFactory
{
    public static Icon Create(QuotaSeverity severity)
    {
        using var bitmap = new Bitmap(32, 32, PixelFormat.Format32bppArgb);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.Clear(Color.Transparent);

        using var background = new SolidBrush(Color.FromArgb(35, 39, 55));
        using var ring = new Pen(Color.FromArgb(232, 234, 246), 3.2f);
        graphics.FillEllipse(background, 2, 2, 28, 28);
        graphics.DrawEllipse(ring, 5, 5, 20, 20);
        graphics.DrawLine(ring, 20, 20, 27, 27);

        var statusColor = severity switch
        {
            QuotaSeverity.Critical => Color.FromArgb(244, 92, 92),
            QuotaSeverity.Warning => Color.FromArgb(244, 190, 75),
            QuotaSeverity.Loading => Color.FromArgb(132, 147, 188),
            _ => Color.FromArgb(113, 220, 143),
        };
        using var statusBrush = new SolidBrush(statusColor);
        graphics.FillEllipse(statusBrush, 21, 2, 9, 9);

        var handle = bitmap.GetHicon();
        try
        {
            using var icon = Icon.FromHandle(handle);
            return (Icon)icon.Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyIcon(IntPtr handle);
}
