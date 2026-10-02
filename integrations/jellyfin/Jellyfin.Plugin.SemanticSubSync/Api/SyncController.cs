using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using Jellyfin.Plugin.SemanticSubSync.Engine;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync.Api;

/// <summary>A sync started from an item's menu. Runs in the background: a request held open for
/// minutes (first use installs the engine) would be cut by many reverse proxies.</summary>
public sealed class SyncJob
{
    public Guid Id { get; } = Guid.NewGuid();

    public bool Done { get; set; }

    public IReadOnlyList<SyncJobResult> Results { get; set; } = [];

    public DateTime Started { get; } = DateTime.UtcNow;
}

public sealed record SyncJobResult(string Subtitle, string Status);

/// <summary>Endpoints behind the "Sync subtitles" entry of the item menu (administrators only).</summary>
[ApiController]
[Route("SemanticSubSync")]
[Authorize(Policy = "RequiresElevation")]
public sealed class SyncController : ControllerBase
{
    private static readonly ConcurrentDictionary<Guid, SyncJob> Jobs = new();

    private readonly ILibraryManager _library;
    private readonly SyncService _sync;
    private readonly EngineInstaller _installer;
    private readonly ILogger<SyncController> _logger;

    public SyncController(ILibraryManager library, SyncService sync, EngineInstaller installer, ILogger<SyncController> logger)
    {
        _library = library;
        _sync = sync;
        _installer = installer;
        _logger = logger;
    }

    /// <summary>Where the engine stands: not_installed, installing, ready or failed (shown on the plugin page).</summary>
    [HttpGet("Status")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public ActionResult<EngineStatus> Status() => _installer.Status;

    /// <summary>Starts re-timing every external subtitle of a movie or an episode.</summary>
    [HttpPost("Items/{itemId}/Sync")]
    [ProducesResponseType(StatusCodes.Status202Accepted)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult<SyncJob> Sync([FromRoute] Guid itemId)
    {
        if (_library.GetItemById(itemId) is not Video { IsVirtualItem: false } video || string.IsNullOrEmpty(video.Path))
        {
            return NotFound();
        }

        foreach (var old in Jobs.Values.Where(j => j.Done && j.Started < DateTime.UtcNow.AddHours(-1)).ToList())
        {
            Jobs.TryRemove(old.Id, out _);
        }

        var job = new SyncJob();
        Jobs[job.Id] = job;
        _ = Task.Run(async () =>
        {
            try
            {
                var results = await _sync.SyncNowAsync(video, CancellationToken.None).ConfigureAwait(false);
                job.Results = results.Select(r => new SyncJobResult(r.Subtitle, r.Status)).ToList();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "SemanticSubSync: sync of {Item} failed", video.Path);
                job.Results = [new SyncJobResult(string.Empty, "error")];
            }
            finally
            {
                job.Done = true;
            }
        });
        return Accepted(job);
    }

    /// <summary>The state of a sync started with POST Items/{itemId}/Sync.</summary>
    [HttpGet("Jobs/{jobId}")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult<SyncJob> Job([FromRoute] Guid jobId) =>
        Jobs.TryGetValue(jobId, out var job) ? job : NotFound();
}
