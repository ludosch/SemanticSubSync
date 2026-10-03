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
/// while someone is watching, the work waits. The first scan of a new library adds every video: the
/// library is recorded first (see <see cref="SyncService.BaselineIfNew"/>), so only catch-up processes
/// its existing subtitles.</summary>
public sealed class NewVideoListener : IHostedService, IDisposable
{
    private static readonly TimeSpan Settle = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan NoEngine = TimeSpan.FromHours(1);   // the installer's own retry delay

    private readonly ILibraryManager _library;
    private readonly SyncService _sync;
    private readonly ILogger<NewVideoListener> _logger;
    private readonly ConcurrentDictionary<Guid, byte> _pending = new();
    private readonly CancellationTokenSource _stop = new();
    private readonly object _drainLock = new();
    private readonly Timer _timer;
    private Task _drain = Task.CompletedTask;

    public NewVideoListener(ILibraryManager library, SyncService sync, ILogger<NewVideoListener> logger)
    {
        _library = library;
        _sync = sync;
        _logger = logger;
        _timer = new Timer(_ => StartDrain(), null, Timeout.Infinite, Timeout.Infinite);
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        _library.ItemAdded += OnItemAdded;
        return Task.CompletedTask;
    }

    public async Task StopAsync(CancellationToken cancellationToken)
    {
        _library.ItemAdded -= OnItemAdded;
        _timer.Change(Timeout.Infinite, Timeout.Infinite);
        await _stop.CancelAsync().ConfigureAwait(false);
        Task drain;
        lock (_drainLock)
        {
            drain = _drain;
        }

        try
        {
            await drain.WaitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Jellyfin stops waiting; the engine process is killed by the cancellation
        }
    }

    public void Dispose()
    {
        _timer.Dispose();
        _stop.Dispose();
    }

    private void OnItemAdded(object? sender, ItemChangeEventArgs e)
    {
        if (e.Item is Video { IsVirtualItem: false } && Plugin.Instance is not null)
        {
            _pending[e.Item.Id] = 0;
            _timer.Change(Settle, Timeout.InfiniteTimeSpan);
        }
    }

    private void StartDrain()
    {
        lock (_drainLock)
        {
            if (!_drain.IsCompleted || _stop.IsCancellationRequested)
            {
                return;   // the running pass picks up what was added meanwhile
            }

            _drain = Task.Run(DrainAsync);
        }
    }

    private async Task DrainAsync()
    {
        var next = Settle;
        try   // an exception escaping the background task would go unnoticed
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
                    if (_library.GetItemById(id) is not Video video || string.IsNullOrEmpty(video.Path) || !_sync.InChosenLibrary(video))
                    {
                        continue;
                    }

                    var outcome = await _sync.ProcessAsync(video, _stop.Token).ConfigureAwait(false);
                    if (outcome == SyncOutcome.Postponed)
                    {
                        _pending[id] = 0;   // the outer loop waits for playback to end
                        break;
                    }

                    if (outcome == SyncOutcome.EngineUnavailable)
                    {
                        _pending[id] = 0;
                        next = NoEngine;
                        _logger.LogWarning("SemanticSubSync: no engine to process the new videos; tried again in {Hours} h", NoEngine.TotalHours);
                        return;
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
            if (!_pending.IsEmpty && !_stop.IsCancellationRequested)
            {
                _timer.Change(next, Timeout.InfiniteTimeSpan);   // added after the last check, or waiting for the engine
            }
        }
    }
}
