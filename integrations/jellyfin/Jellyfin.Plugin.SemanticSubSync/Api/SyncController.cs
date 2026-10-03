using System;
using Jellyfin.Plugin.SemanticSubSync.Engine;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.SemanticSubSync.Api;

/// <summary>Endpoints behind the "Sync subtitles" entry of the item menu (administrators only).</summary>
[ApiController]
[Route("SemanticSubSync")]
[Authorize(Policy = "RequiresElevation")]
public sealed class SyncController : ControllerBase
{
    private readonly ILibraryManager _library;
    private readonly SyncJobs _jobs;
    private readonly EngineInstaller _installer;

    public SyncController(ILibraryManager library, SyncJobs jobs, EngineInstaller installer)
    {
        _library = library;
        _jobs = jobs;
        _installer = installer;
    }

    /// <summary>Where the engine stands: not_installed, installing, ready or failed (shown on the plugin page).</summary>
    [HttpGet("Status")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    public ActionResult<EngineStatus> Status() => _installer.Status;

    /// <summary>Starts re-timing every external subtitle of a movie or an episode; while one runs for
    /// this item, returns it instead of starting another.</summary>
    [HttpPost("Items/{itemId}/Sync")]
    [ProducesResponseType(StatusCodes.Status202Accepted)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult<SyncJob> Sync([FromRoute] Guid itemId)
    {
        if (_library.GetItemById(itemId) is not Video { IsVirtualItem: false } video || string.IsNullOrEmpty(video.Path))
        {
            return NotFound();
        }

        return Accepted(_jobs.Start(video));
    }

    /// <summary>The state of a sync started with POST Items/{itemId}/Sync.</summary>
    [HttpGet("Jobs/{jobId}")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult<SyncJob> Job([FromRoute] Guid jobId) =>
        _jobs.Get(jobId) is { } job ? job : NotFound();
}
