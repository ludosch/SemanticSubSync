using System;
using System.Collections.Concurrent;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync;

/// <summary>Processes the subtitles of a movie or episode as soon as it is added to the library.
/// Items are collected for a short while (a scan adds many at once), then handled in the background;
/// while someone is watching, the work waits.</summary>
public sealed class NewVideoListener : IHostedService, IDisposable
{
    private static readonly TimeSpan Settle = TimeSpan.FromSeconds(30);

    private readonly ILibraryManager _library;
    private readonly SyncService _sync;
    private readonly ILogger<NewVideoListener> _logger;
    private readonly ConcurrentDictionary<Guid, byte> _pending = new();
    private readonly CancellationTokenSource _stop = new();
    private readonly Timer _timer;
    private int _running;

    public NewVideoListener(ILibraryManager library, SyncService sync, ILogger<NewVideoListener> logger)
    {
        _library = library;
        _sync = sync;
        _logger = logger;
        _timer = new Timer(_ => _ = DrainAsync(), null, Timeout.Infinite, Timeout.Infinite);
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        _library.ItemAdded += OnItemAdded;
        return Task.CompletedTask;
    }

    public async Task StopAsync(CancellationToken cancellationToken)
    {
        _library.ItemAdded -= OnItemAdded;
        await _stop.CancelAsync().ConfigureAwait(false);
    }

    public void Dispose()
    {
        _timer.Dispose();
        _stop.Dispose();
    }

    private void OnItemAdded(object? sender, ItemChangeEventArgs e)
    {
        if (e.Item is Video { IsVirtualItem: false } && Plugin.Instance?.Configuration.Libraries.Length > 0)
        {
            _pending[e.Item.Id] = 0;
            _timer.Change(Settle, Timeout.InfiniteTimeSpan);
        }
    }

    private async Task DrainAsync()
    {
        if (Interlocked.Exchange(ref _running, 1) == 1)
        {
            return;   // the running pass picks up what was added meanwhile
        }

        try   // an exception escaping a timer callback would stop the server
        {
            while (!_pending.IsEmpty && !_stop.IsCancellationRequested)
            {
                while (_sync.SomeoneIsWatching())
                {
                    await Task.Delay(TimeSpan.FromMinutes(1), _stop.Token).ConfigureAwait(false);
                }

                foreach (var id in _pending.Keys)
                {
                    _pending.TryRemove(id, out _);
                    if (_library.GetItemById(id) is Video video && !string.IsNullOrEmpty(video.Path) && _sync.InChosenLibrary(video)
                        && !await _sync.ProcessAsync(video, _stop.Token).ConfigureAwait(false))
                    {
                        _pending[id] = 0;   // interrupted by playback: try again once it ends
                        break;
                    }
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "SemanticSubSync: processing new videos failed");
        }
        finally
        {
            Interlocked.Exchange(ref _running, 0);
            if (!_pending.IsEmpty && !_stop.IsCancellationRequested)
            {
                _timer.Change(Settle, Timeout.InfiniteTimeSpan);   // added after the last check
            }
        }
    }
}
