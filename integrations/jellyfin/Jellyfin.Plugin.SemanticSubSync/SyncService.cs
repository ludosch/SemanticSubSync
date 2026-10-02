using System;
using System.Collections.Concurrent;
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
    private readonly ConcurrentDictionary<string, byte> _warnedReadOnly = new();
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

    /// <summary>The chosen libraries that still exist.</summary>
    public IReadOnlyList<Guid> ChosenLibraries()
    {
        var config = Plugin.Instance?.Configuration;
        return config is null
            ? []
            : _library.GetUserRootFolder().Children.Select(f => f.Id).Where(config.IsChosen).ToList();
    }

    /// <summary>Every movie and episode of a library that is a real file.</summary>
    public IReadOnlyList<Video> Videos(Guid libraryId) =>
        _library.GetItemList(new InternalItemsQuery
        {
            IncludeItemTypes = [BaseItemKind.Movie, BaseItemKind.Episode],
            IsVirtualItem = false,
            Recursive = true,
            ParentId = libraryId,
        }).OfType<Video>().Where(v => !string.IsNullOrEmpty(v.Path) && File.Exists(v.Path)).ToList();

    /// <summary>Whether the video belongs to a library that was chosen.</summary>
    public bool InChosenLibrary(Video video)
    {
        var config = Plugin.Instance?.Configuration;
        return config is not null && _library.GetCollectionFolders(video).Any(f => config.IsChosen(f.Id));
    }

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

        var done = await RunAsync(video, todo, waitForNoPlayback: true, ct).ConfigureAwait(false);
        return done is not null && done.Count == todo.Count;
    }

    /// <summary>Asked from the item's menu: every external subtitle of the video, whatever its
    /// library, now. Returns (file name, decision) per subtitle, or the reason nothing ran.</summary>
    public async Task<IReadOnlyList<(string Subtitle, string Status)>> SyncNowAsync(Video video, CancellationToken ct)
    {
        var subs = SubtitlesOf(video.Path).ToList();
        if (subs.Count == 0)
        {
            return [(string.Empty, "no_subtitle")];
        }

        return await RunAsync(video, subs, waitForNoPlayback: false, ct).ConfigureAwait(false)
            ?? [(string.Empty, "engine_unavailable")];
    }

    /// <summary>Runs the engine on `subs`, one at a time. Stops before a subtitle when someone is
    /// watching (if asked). Null when the engine cannot be installed.</summary>
    private async Task<List<(string Subtitle, string Status)>?> RunAsync(Video video, List<string> subs, bool waitForNoPlayback, CancellationToken ct)
    {
        var results = new List<(string Subtitle, string Status)>();
        if (!CanWrite(Path.GetDirectoryName(video.Path)!))
        {
            if (_warnedReadOnly.TryAdd(Path.GetDirectoryName(video.Path)!, 0))   // once per folder, not every run
            {
                _logger.LogWarning("SemanticSubSync: cannot write in the folder of {Video}: is the media library mounted read-only (:ro)? Skipped", video.Path);
            }

            subs.ForEach(s => results.Add((Path.GetFileName(s), "read_only")));
            return results;
        }

        if (!await _installer.EnsureAsync(ct).ConfigureAwait(false))
        {
            return null;
        }

        await _oneAtATime.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var changed = false;
            foreach (var sub in subs)
            {
                if (waitForNoPlayback && SomeoneIsWatching())
                {
                    _logger.LogInformation("SemanticSubSync: playback in progress, postponed");
                    break;
                }

                var result = await _runner.RunAsync(video.Path, sub, ct).ConfigureAwait(false);
                if (result is null)
                {
                    results.Add((Path.GetFileName(sub), "error"));   // logged by the runner; tried again on the next run
                    continue;
                }

                _logger.LogInformation("SemanticSubSync: {Status} {Subtitle}", result.Status, sub);
                results.Add((Path.GetFileName(sub), result.Status));
                changed |= result.FilesChanged;
                MarkSeen(sub);
            }

            if (changed)
            {
                _providers.QueueRefresh(video.Id, new MetadataRefreshOptions(new DirectoryService(_fileSystem)), RefreshPriority.High);
            }

            return results;
        }
        finally
        {
            _oneAtATime.Release();
        }
    }

    private static string BaselinePath => Path.Combine(EngineInstaller.DataDir, "libraries.json");

    /// <summary>The first time a library is seen, records its subtitles as existing ones: they are left
    /// alone while ProcessExisting is off, and processed by the next runs once it is on (catch-up).
    /// Returns true when it recorded some.</summary>
    public bool BaselineIfNew(Guid libraryId, IReadOnlyList<Video> videos)
    {
        lock (_seenLock)
        {
            var done = File.Exists(BaselinePath)
                ? JsonSerializer.Deserialize<HashSet<Guid>>(File.ReadAllText(BaselinePath)) ?? []
                : [];
            if (!done.Add(libraryId))
            {
                return false;
            }

            var count = 0;
            var seen = Seen();
            foreach (var sub in videos.SelectMany(v => SubtitlesOf(v.Path)))
            {
                count += seen.TryAdd(sub, Existing + SizeAndDate(sub)) ? 1 : 0;
            }

            SaveSeen(seen);
            _logger.LogInformation("SemanticSubSync: library {Library}: {Count} existing subtitles recorded, {What}",
                _library.GetItemById(libraryId)?.Name ?? libraryId.ToString(), count,
                Plugin.Instance?.Configuration.ProcessExisting == true ? "to be processed (catch-up is on)" : "left as they are");

            File.WriteAllText(BaselinePath, JsonSerializer.Serialize(done));
            return count > 0;
        }
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

    // A subtitle recorded when its library was first seen, never processed: "existing:<size>:<date>".
    private const string Existing = "existing:";

    private static string SizeAndDate(string path)
    {
        var fi = new FileInfo(path);
        return string.Create(CultureInfo.InvariantCulture, $"{fi.Length}:{fi.LastWriteTimeUtc.Ticks}");
    }

    private static string Fingerprint(string path) => SizeAndDate(path) + ":" + Plugin.Instance?.Configuration.OutputMode;

    /// <summary>Nothing to do for this subtitle: processed and unchanged since, or an existing one left
    /// alone (unchanged, catch-up off). A changed file is processed whatever its past.</summary>
    private bool IsSeen(string sub)
    {
        lock (_seenLock)
        {
            if (!Seen().TryGetValue(sub, out var fp))
            {
                return false;
            }

            return fp.StartsWith(Existing, StringComparison.Ordinal)
                ? Plugin.Instance?.Configuration.ProcessExisting != true && fp == Existing + SizeAndDate(sub)
                : fp == Fingerprint(sub);
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
