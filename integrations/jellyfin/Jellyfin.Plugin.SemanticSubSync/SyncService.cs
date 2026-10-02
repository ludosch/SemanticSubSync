using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Data.Enums;
using Jellyfin.Plugin.SemanticSubSync.Engine;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Controller.Session;
using MediaBrowser.Model.IO;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync;

/// <summary>Finds the external subtitles of movies and episodes and hands the new or changed ones to
/// the engine, one at a time, never while someone is watching. After a run that touched files, the
/// item is refreshed so Jellyfin shows the result without waiting for a library scan.</summary>
public sealed class SyncService : IDisposable
{
    private static readonly string[] OwnTags = ["replaced", "resync"];   // files the engine writes

    private readonly ILibraryManager _library;
    private readonly ISessionManager _sessions;
    private readonly IProviderManager _providers;
    private readonly IFileSystem _fileSystem;
    private readonly EngineInstaller _installer;
    private readonly EngineRunner _runner;
    private readonly ILogger<SyncService> _logger;
    private readonly SemaphoreSlim _oneAtATime = new(1, 1);
    private readonly object _seenLock = new();
    private Dictionary<string, string>? _seen;

    public SyncService(ILibraryManager library, ISessionManager sessions, IProviderManager providers, IFileSystem fileSystem,
                       EngineInstaller installer, EngineRunner runner, ILogger<SyncService> logger)
    {
        _library = library;
        _sessions = sessions;
        _providers = providers;
        _fileSystem = fileSystem;
        _installer = installer;
        _runner = runner;
        _logger = logger;
    }

    public void Dispose() => _oneAtATime.Dispose();

    private static string SeenPath => Path.Combine(EngineInstaller.DataDir, "seen.json");

    /// <summary>Every movie and episode that is a real file.</summary>
    public IReadOnlyList<Video> Videos() =>
        _library.GetItemList(new InternalItemsQuery
        {
            IncludeItemTypes = [BaseItemKind.Movie, BaseItemKind.Episode],
            IsVirtualItem = false,
            Recursive = true,
        }).OfType<Video>().Where(v => !string.IsNullOrEmpty(v.Path) && File.Exists(v.Path)).ToList();

    public bool SomeoneIsWatching() => _sessions.Sessions.Any(s => s.NowPlayingItem is not null);

    /// <summary>The external .srt files that belong to `video` (same file name stem), ours excluded.</summary>
    public static IEnumerable<string> SubtitlesOf(string video)
    {
        var dir = Path.GetDirectoryName(video);
        if (dir is null || !Directory.Exists(dir))
        {
            return [];
        }

        var stem = Path.GetFileNameWithoutExtension(video) + ".";
        return Directory.EnumerateFiles(dir, "*.srt")
            .Where(f => Path.GetFileName(f).StartsWith(stem, StringComparison.Ordinal) && !IsOwn(f))
            .Order(StringComparer.Ordinal);
    }

    public static bool IsOwn(string path)
    {
        var toks = Path.GetFileName(path).ToLowerInvariant().Split('.');
        return toks.Skip(1).Take(toks.Length - 2).Any(t => OwnTags.Contains(t));
    }

    /// <summary>Processes the new or changed subtitles of one video. Returns false when it stopped
    /// early (someone started watching, engine unavailable): the rest is left for the next run.</summary>
    public async Task<bool> ProcessAsync(Video video, CancellationToken ct)
    {
        var todo = SubtitlesOf(video.Path).Where(s => !IsSeen(s)).ToList();
        if (todo.Count == 0)
        {
            return true;
        }

        if (!CanWrite(Path.GetDirectoryName(video.Path)!))
        {
            _logger.LogWarning("SemanticSubSync: cannot write in the folder of {Video}: is the media library mounted read-only (:ro)? Skipped", video.Path);
            return true;
        }

        if (!await _installer.EnsureAsync(ct).ConfigureAwait(false))
        {
            return false;
        }

        await _oneAtATime.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var changed = false;
            foreach (var sub in todo)
            {
                if (SomeoneIsWatching())
                {
                    _logger.LogInformation("SemanticSubSync: playback in progress, postponed");
                    return false;
                }

                var result = await _runner.RunAsync(video.Path, sub, ct).ConfigureAwait(false);
                if (result is null)
                {
                    continue;   // error, logged by the runner; tried again on the next run
                }

                _logger.LogInformation("SemanticSubSync: {Status} {Subtitle}", result.Status, sub);
                changed |= result.FilesChanged;
                MarkSeen(sub);
            }

            if (changed)
            {
                _providers.QueueRefresh(video.Id, new MetadataRefreshOptions(new DirectoryService(_fileSystem)), RefreshPriority.High);
            }

            return true;
        }
        finally
        {
            _oneAtATime.Release();
        }
    }

    private static string BaselinePath => Path.Combine(EngineInstaller.DataDir, "baseline.done");

    /// <summary>On the very first run, unless ProcessExisting is on, records the subtitles already
    /// there as seen without processing them. Returns true when it did so.</summary>
    public bool BaselineIfFirstRun(IReadOnlyList<Video> videos)
    {
        if (File.Exists(BaselinePath))
        {
            return false;
        }

        var count = 0;
        if (Plugin.Instance?.Configuration.ProcessExisting != true)
        {
            lock (_seenLock)
            {
                var seen = Seen();
                foreach (var sub in videos.SelectMany(v => SubtitlesOf(v.Path)))
                {
                    seen.TryAdd(sub, Fingerprint(sub));
                    count++;
                }

                SaveSeen(seen);
            }

            _logger.LogInformation("SemanticSubSync: first run, {Count} existing subtitles recorded and left as they are", count);
        }

        Directory.CreateDirectory(EngineInstaller.DataDir);
        File.WriteAllText(BaselinePath, DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture));
        return count > 0;
    }

    private static bool CanWrite(string dir)
    {
        var probe = Path.Combine(dir, $".semantic-subsync-{Guid.NewGuid():N}.tmp");
        try
        {
            File.WriteAllBytes(probe, []);
            File.Delete(probe);
            return true;
        }
        catch (Exception ex) when (ex is UnauthorizedAccessException or IOException)
        {
            return false;
        }
    }

    // ---- seen: (size, mtime, output mode) of each subtitle after its last run, so unchanged files cost
    // nothing. The engine's own state.db is the reference; this only avoids starting a process per file.

    private static string Fingerprint(string path)
    {
        var fi = new FileInfo(path);
        var mode = Plugin.Instance?.Configuration.OutputMode;
        return string.Create(CultureInfo.InvariantCulture, $"{fi.Length}:{fi.LastWriteTimeUtc.Ticks}:{mode}");
    }

    private bool IsSeen(string sub)
    {
        lock (_seenLock)
        {
            return Seen().TryGetValue(sub, out var fp) && fp == Fingerprint(sub);
        }
    }

    private void MarkSeen(string sub)
    {
        lock (_seenLock)
        {
            var seen = Seen();
            if (File.Exists(sub))
            {
                seen[sub] = Fingerprint(sub);
            }

            SaveSeen(seen);
        }
    }

    private static void SaveSeen(Dictionary<string, string> seen)
    {
        Directory.CreateDirectory(EngineInstaller.DataDir);
        var tmp = SeenPath + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(seen));
        File.Move(tmp, SeenPath, overwrite: true);
    }

    private Dictionary<string, string> Seen()
    {
        if (_seen is null)
        {
            try
            {
                _seen = File.Exists(SeenPath)
                    ? JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(SeenPath)) ?? []
                    : [];
            }
            catch (JsonException)
            {
                _seen = [];
            }
        }

        return _seen;
    }
}
