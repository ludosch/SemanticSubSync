using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Model.Tasks;

namespace Jellyfin.Plugin.SemanticSubSync;

/// <summary>Scheduled task: goes through every movie and episode and re-times the external
/// subtitles that are new or changed since the last run (e.g. downloaded by Bazarr).</summary>
public sealed class SyncNewSubtitlesTask : IScheduledTask
{
    private readonly SyncService _sync;

    public SyncNewSubtitlesTask(SyncService sync)
    {
        _sync = sync;
    }

    public string Name => "Re-time new subtitles";

    public string Key => "SemanticSubSyncNewSubtitles";

    public string Description => "Re-times the external subtitles added or changed since the last run, on the subtitle embedded in each video.";

    public string Category => "SemanticSubSync";

    public async Task ExecuteAsync(IProgress<double> progress, CancellationToken cancellationToken)
    {
        if (Plugin.Instance?.Configuration.Enabled != true || _sync.SomeoneIsWatching())
        {
            return;
        }

        var videos = _sync.Videos();
        if (_sync.BaselineIfFirstRun(videos))
        {
            return;
        }

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
