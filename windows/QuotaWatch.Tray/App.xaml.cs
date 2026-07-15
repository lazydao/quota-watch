using System.Threading;
using System.Windows;

namespace QuotaWatch.Tray;

public partial class App : System.Windows.Application
{
    private const string MutexName = @"Local\QuotaWatch.Tray";
    private const string ShowEventName = @"Local\QuotaWatch.Tray.Show";

    private Mutex? _singleInstanceMutex;
    private EventWaitHandle? _showRequestEvent;
    private CancellationTokenSource? _showRequestCancellation;
    private Task? _showRequestTask;
    private TrayApplicationController? _controller;
    private bool _ownsMutex;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        var backgroundOnly = e.Args.Contains("--background", StringComparer.OrdinalIgnoreCase);
        _singleInstanceMutex = new Mutex(true, MutexName, out var createdNew);
        _ownsMutex = createdNew;
        _showRequestEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
        if (!createdNew)
        {
            if (!backgroundOnly)
            {
                _showRequestEvent.Set();
            }

            Shutdown();
            return;
        }

        _controller = new TrayApplicationController();
        await _controller.InitializeAsync();
        _showRequestCancellation = new CancellationTokenSource();
        _showRequestTask = ListenForShowRequestsAsync(_showRequestCancellation.Token);
        if (!backgroundOnly)
        {
            _controller.ShowWindow();
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _controller?.Dispose();
        _showRequestCancellation?.Cancel();
        _showRequestEvent?.Set();
        try
        {
            _showRequestTask?.Wait(TimeSpan.FromSeconds(1));
        }
        catch (AggregateException)
        {
        }
        _showRequestCancellation?.Dispose();
        _showRequestEvent?.Dispose();

        if (_singleInstanceMutex is not null && _ownsMutex)
        {
            _singleInstanceMutex.ReleaseMutex();
        }

        _singleInstanceMutex?.Dispose();

        base.OnExit(e);
    }

    private async Task ListenForShowRequestsAsync(CancellationToken cancellationToken)
    {
        if (_showRequestEvent is null)
        {
            return;
        }

        while (!cancellationToken.IsCancellationRequested)
        {
            await Task.Run(() => _showRequestEvent.WaitOne(), cancellationToken);
            if (!cancellationToken.IsCancellationRequested)
            {
                await Dispatcher.InvokeAsync(() => _controller?.ShowWindow());
            }
        }
    }
}
