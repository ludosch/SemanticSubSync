using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Model.Tasks;

namespace Jellyfin.Plugin.SemanticSubSync;

/// <summary>Scheduled task: goes through the movies and episodes of the chosen libraries and
/// re-times the external subtitles that are new or changed since the last run (e.g. downloaded
/// by Bazarr).</summary>
public sealed class SyncNewSubtitlesTask : IScheduledTask
{
    private readonly SyncService _sync;

    public SyncNewSubtitlesTask(SyncService sync)
    {
        _sync = sync;
    }

    public string Name => "Re-time new subtitles";

    public string Key => "SemanticSubSyncNewSubtitles";

    public string Description => "Re-times the external subtitles added or changed since the last run in the chosen libraries, on the subtitle embedded in each video.";

    public string Category => "SemanticSubSync";

    public async Task ExecuteAsync(IProgress<double> progress, CancellationToken cancellationToken)
    {
        if (_sync.SomeoneIsWatching())
        {
            return;
        }

        var videos = new List<Video>();
        foreach (var lib in _sync.ChosenLibraries())
        {
            var inLib = _sync.Videos(lib);
            if (!_sync.BaselineIfNew(lib, inLib))
            {
                videos.AddRange(inLib);
            }
        }

        videos = videos.DistinctBy(v => v.Id).ToList();
        for (var i = 0; i < videos.Count; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!await _sync.ProcessAsync(videos[i], cancellationToken).ConfigureAwait(false))
            {
                return;   // someone is watching or the engine is unavailable: the next run continues
            }

            progress.Report(100.0 * (i + 1) / videos.Count);
        }
    }

    public IEnumerable<TaskTriggerInfo> GetDefaultTriggers() =>
    [
        new TaskTriggerInfo { Type = TaskTriggerInfoType.IntervalTrigger, IntervalTicks = TimeSpan.FromMinutes(30).Ticks },
    ];
}
