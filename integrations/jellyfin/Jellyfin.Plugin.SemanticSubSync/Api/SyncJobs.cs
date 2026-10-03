using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync.Api;

/// <summary>A sync started from an item's menu. Never changed once published: a new state is a new
/// record.</summary>
public sealed record SyncJob(Guid Id, Guid ItemId, DateTime Started, bool Done, IReadOnlyList<SyncJobResult> Results);

public sealed record SyncJobResult(string Subtitle, string Status);

/// <summary>The syncs started from the item menu. They run in the background: a request held open for
/// minutes (first use installs the engine) would be cut by many reverse proxies. They stop when
/// Jellyfin stops; asking again for an item whose sync is still running returns that sync.</summary>
public sealed class SyncJobs
{
    private static readonly TimeSpan Keep = TimeSpan.FromHours(1);

    private readonly SyncService _sync;
    private readonly IHostApplicationLifetime _lifetime;
    private readonly ILogger<SyncJobs> _logger;
    private readonly ConcurrentDictionary<Guid, SyncJob> _jobs = new();
    private readonly object _startLock = new();

    public SyncJobs(SyncService sync, IHostApplicationLifetime lifetime, ILogger<SyncJobs> logger)
    {
        _sync = sync;
        _lifetime = lifetime;
        _logger = logger;
    }

    public SyncJob Start(Video video)
    {
        Prune();
        lock (_startLock)
        {
            var running = _jobs.Values.FirstOrDefault(j => !j.Done && j.ItemId == video.Id);
            if (running is not null)
            {
                return running;
            }

            var job = new SyncJob(Guid.NewGuid(), video.Id, DateTime.UtcNow, false, []);
            _jobs[job.Id] = job;
            _ = Task.Run(() => RunAsync(job, video));
            return job;
        }
    }

    public SyncJob? Get(Guid id)
    {
        Prune();
        return _jobs.TryGetValue(id, out var job) ? job : null;
    }

    private async Task RunAsync(SyncJob job, Video video)
    {
        IReadOnlyList<SyncJobResult> results;
        try
        {
            var r = await _sync.SyncNowAsync(video, _lifetime.ApplicationStopping).ConfigureAwait(false);
            results = r.Select(x => new SyncJobResult(x.Subtitle, x.Status)).ToList();
        }
        catch (OperationCanceledException) when (_lifetime.ApplicationStopping.IsCancellationRequested)
        {
            results = [new SyncJobResult(string.Empty, "cancelled")];
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "SemanticSubSync: sync of {Item} failed", video.Path);
            results = [new SyncJobResult(string.Empty, "error")];
        }

        _jobs[job.Id] = job with { Done = true, Results = results };
    }

    private void Prune()
    {
        var limit = DateTime.UtcNow - Keep;
        foreach (var old in _jobs.Values.Where(j => j.Done && j.Started < limit))
        {
            _jobs.TryRemove(old.Id, out _);
        }
    }
}
