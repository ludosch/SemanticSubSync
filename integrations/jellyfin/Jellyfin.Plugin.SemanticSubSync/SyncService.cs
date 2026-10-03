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

/// <summary>How an automatic pass over one video ended.</summary>
public enum SyncOutcome
{
    /// <summary>Every new or changed subtitle was handed to the engine (or nothing was to do).</summary>
    Done,

    /// <summary>Someone started watching: the rest waits for the next pass.</summary>
    Postponed,

    /// <summary>No engine can run (installation failed): nothing was done.</summary>
    EngineUnavailable,
}

/// <summary>Finds the external subtitles of movies and episodes and hands the new or changed ones to
/// the engine, one at a time; the automatic work does not start a subtitle while someone is watching.
/// After a run that touched files, the item is refreshed so Jellyfin shows the result without waiting
/// for a library scan.</summary>
public sealed class SyncService : IDisposable
{
    private const int MaxAttempts = 3;   // an engine failure on an unchanged file is not retried beyond this

    // files the engine writes: "untouched" (the kept download), "resync" (side mode), "replaced" (the
    // kept download up to 0.12); a hidden kept download ends with ".orig", not a subtitle extension
    private static readonly string[] OwnTags = ["untouched", "replaced", "resync"];

    // decisions that settle a subtitle until it changes; "changed_during_run" is retried, the rest are failures
    private static readonly string[] Final = ["corrected", "in_sync", "unsure", "no_reference", "redundant", "skipped"];

    private readonly ILibraryManager _library;
    private readonly ISessionManager _sessions;
    private readonly IProviderManager _providers;
    private readonly IFileSystem _fileSystem;
    private readonly EngineInstaller _installer;
    private readonly EngineRunner _runner;
    private readonly ILogger<SyncService> _logger;
    private readonly object _seenLock = new();
    private readonly ConcurrentDictionary<string, byte> _warnedReadOnly = new();
    private Dictionary<string, string>? _seen;
    private HashSet<Guid>? _baselined;
    private bool _dirty;

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

    public void Dispose() => Flush();

    private static string SeenPath => Path.Combine(EngineInstaller.DataDir, "seen.json");

    private static string BaselinePath => Path.Combine(EngineInstaller.DataDir, "libraries.json");

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
        if (path.EndsWith(".orig", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        var toks = Path.GetFileName(path).ToLowerInvariant().Split('.');
        return toks.Skip(1).Take(toks.Length - 2).Any(t => OwnTags.Contains(t));
    }

    /// <summary>Processes the new or changed subtitles of one video. The libraries it belongs to are
    /// recorded first if they are new, so the subtitles already there are not taken for new ones.</summary>
    public async Task<SyncOutcome> ProcessAsync(Video video, CancellationToken ct)
    {
        var config = Plugin.Instance?.Configuration;
        foreach (var lib in _library.GetCollectionFolders(video).Where(f => config?.IsChosen(f.Id) == true))
        {
            BaselineIfNew(lib.Id);
        }

        if (SubtitlesOf(video.Path).All(IsSeen))
        {
            return SyncOutcome.Done;
        }

        var (_, outcome) = await RunAsync(video, automatic: true, ct).ConfigureAwait(false);
        return outcome;
    }

    /// <summary>Asked from the item's menu: every external subtitle of the video, whatever its
    /// library, now. Returns (file name, decision) per subtitle, or the reason nothing ran.</summary>
    public async Task<IReadOnlyList<(string Subtitle, string Status)>> SyncNowAsync(Video video, CancellationToken ct)
    {
        if (!SubtitlesOf(video.Path).Any())
        {
            return [(string.Empty, "no_subtitle")];
        }

        var (results, outcome) = await RunAsync(video, automatic: false, ct).ConfigureAwait(false);
        return outcome == SyncOutcome.EngineUnavailable ? [(string.Empty, "engine_unavailable")] : results;
    }

    /// <summary>Runs the engine on the subtitles of `video`, one at a time: automatic, only those not
    /// seen yet (decided once no other run can change that), stopping before a subtitle when someone
    /// is watching; else all of them.</summary>
    private async Task<(List<(string Subtitle, string Status)> Results, SyncOutcome Outcome)> RunAsync(Video video, bool automatic, CancellationToken ct)
    {
        var results = new List<(string Subtitle, string Status)>();
        var dir = Path.GetDirectoryName(video.Path)!;
        if (!CanWrite(dir))
        {
            if (_warnedReadOnly.TryAdd(dir, 0))   // once per folder, not every run
            {
                _logger.LogWarning("SemanticSubSync: cannot write in the folder of {Video}: is the media library mounted read-only (:ro)? Skipped", video.Path);
            }

            results.AddRange(SubtitlesOf(video.Path).Select(s => (Path.GetFileName(s), "read_only")));
            return (results, SyncOutcome.Done);
        }

        if (!await _installer.EnsureAsync(ct).ConfigureAwait(false))
        {
            return (results, SyncOutcome.EngineUnavailable);
        }

        await _installer.RunLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var changed = false;
            var outcome = SyncOutcome.Done;
            foreach (var sub in SubtitlesOf(video.Path).Where(s => !automatic || !IsSeen(s)).ToList())
            {
                if (automatic && SomeoneIsWatching())
                {
                    _logger.LogInformation("SemanticSubSync: playback in progress, postponed");
                    outcome = SyncOutcome.Postponed;
                    break;
                }

                var result = await _runner.RunAsync(video.Path, sub, ct).ConfigureAwait(false);
                results.Add((Path.GetFileName(sub), result.Status));
                changed |= result.FilesChanged;
                Record(sub, result.Status);
            }

            if (changed)
            {
                _providers.QueueRefresh(video.Id, new MetadataRefreshOptions(new DirectoryService(_fileSystem)), RefreshPriority.High);
            }

            return (results, outcome);
        }
        finally
        {
            _installer.RunLock.Release();
            Flush();   // once per video: a crash loses at most this one
        }
    }

    private void Record(string sub, string status)
    {
        if (status.StartsWith("unchanged", StringComparison.Ordinal) || Final.Contains(status))
        {
            _logger.LogInformation("SemanticSubSync: {Status} {Subtitle}", status, sub);
            MarkSeen(sub);
        }
        else if (status == "changed_during_run")
        {
            _logger.LogInformation("SemanticSubSync: {Subtitle} changed while it was processed: taken again next time", sub);
        }
        else
        {
            if (status is not ("error" or "timeout"))
            {
                _logger.LogWarning("SemanticSubSync: unknown decision {Status} on {Subtitle}: taken as a failure", status, sub);
            }

            MarkFailed(sub);
        }
    }

    /// <summary>The first time a library is seen, records the subtitles already in its folders: they
    /// are left alone while ProcessExisting is off, and processed by the next runs once it is on
    /// (catch-up). Read from disk, not from the library, which may still be scanning.</summary>
    public void BaselineIfNew(Guid libraryId)
    {
        lock (_seenLock)
        {
            if (Baselined().Contains(libraryId))
            {
                return;
            }
        }

        var lib = _library.GetItemById(libraryId);
        var roots = lib?.PhysicalLocations.Where(Directory.Exists).ToList() ?? [];
        if (roots.Count == 0)
        {
            return;   // not reachable now (unmounted share?): recorded once it is
        }

        var subs = roots
            .SelectMany(r => Directory.EnumerateFiles(r, "*.srt", new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true }))
            .Where(s => !IsOwn(s))
            .Select(s => (Path: s, Stamp: Existing + SizeAndDate(s)))
            .ToList();

        lock (_seenLock)
        {
            if (!Baselined().Add(libraryId))
            {
                return;   // recorded meanwhile by another pass
            }

            var seen = Seen();
            var count = subs.Count(s => seen.TryAdd(s.Path, s.Stamp));
            _dirty = true;   // seen.json must exist once a library is recorded, see Seen()
            FlushLocked();
            WriteAtomic(BaselinePath, JsonSerializer.Serialize(_baselined));
            _logger.LogInformation("SemanticSubSync: library {Library}: {Count} existing subtitles recorded, {What}",
                lib?.Name ?? libraryId.ToString(), count,
                Plugin.Instance?.Configuration.ProcessExisting == true ? "to be processed (catch-up is on)" : "left as they are");
        }
    }

    /// <summary>Forgets the subtitles deleted or renamed since. A file is forgotten only when its folder
    /// (or the folder above, not empty) is still there: an unmounted share does not erase the record.</summary>
    public void PruneMissing()
    {
        List<string> keys;
        lock (_seenLock)
        {
            keys = Seen().Keys.ToList();
        }

        var gone = keys.Where(k => !File.Exists(k) && FolderStillThere(k)).ToList();
        if (gone.Count == 0)
        {
            return;
        }

        lock (_seenLock)
        {
            var seen = Seen();
            gone.ForEach(k => seen.Remove(k));
            _dirty = true;
            FlushLocked();
        }

        _logger.LogInformation("SemanticSubSync: {Count} deleted or renamed subtitles forgotten", gone.Count);
    }

    private static bool FolderStillThere(string file)
    {
        var dir = Path.GetDirectoryName(file);
        if (dir is null)
        {
            return false;
        }

        if (Directory.Exists(dir))
        {
            return true;
        }

        var parent = Path.GetDirectoryName(dir);
        return parent is not null && Directory.Exists(parent) && Directory.EnumerateFileSystemEntries(parent).Any();
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

    // ---- seen: (size, mtime, output mode, kept download visible or hidden) of each subtitle after its
    // last run, so unchanged files cost nothing. The engine's own state.db is the reference; this only
    // avoids starting a process per file. A changed setting makes every subtitle checked again: the
    // engine then moves the files (cheap, no new sync).

    // A subtitle recorded when its library was first seen, never processed: "existing:<size>:<date>".
    private const string Existing = "existing:";

    // A subtitle the engine failed on: "failed:<attempts>:<fingerprint>".
    private const string Failed = "failed:";

    private static string SizeAndDate(string path)
    {
        var fi = new FileInfo(path);
        return string.Create(CultureInfo.InvariantCulture, $"{fi.Length}:{fi.LastWriteTimeUtc.Ticks}");
    }

    private static string Fingerprint(string path)
    {
        var config = Plugin.Instance?.Configuration;
        return SizeAndDate(path) + ":" + (config?.Output ?? "replace") + ":" + (config?.KeepDownload ?? "visible");
    }

    private static (int Attempts, string Fingerprint) ParseFailed(string fp)
    {
        var rest = fp[Failed.Length..];
        var colon = rest.IndexOf(':', StringComparison.Ordinal);
        return colon > 0 && int.TryParse(rest[..colon], NumberStyles.None, CultureInfo.InvariantCulture, out var n)
            ? (n, rest[(colon + 1)..])
            : (0, string.Empty);
    }

    /// <summary>Nothing to do for this subtitle: processed and unchanged since, an existing one left
    /// alone (unchanged, catch-up off), or failed on too often (unchanged). A changed file is processed
    /// whatever its past.</summary>
    private bool IsSeen(string sub)
    {
        lock (_seenLock)
        {
            if (!Seen().TryGetValue(sub, out var fp))
            {
                return false;
            }

            if (fp.StartsWith(Existing, StringComparison.Ordinal))
            {
                return Plugin.Instance?.Configuration.ProcessExisting != true && fp == Existing + SizeAndDate(sub);
            }

            if (fp.StartsWith(Failed, StringComparison.Ordinal))
            {
                var (attempts, last) = ParseFailed(fp);
                return attempts >= MaxAttempts && last == Fingerprint(sub);
            }

            return fp == Fingerprint(sub);
        }
    }

    private void MarkSeen(string sub)
    {
        lock (_seenLock)
        {
            if (File.Exists(sub))
            {
                Seen()[sub] = Fingerprint(sub);
                _dirty = true;
            }
        }
    }

    private void MarkFailed(string sub)
    {
        if (!File.Exists(sub) || IsSeen(sub))
        {
            return;   // a failed manual run leaves a processed subtitle as it was recorded
        }

        lock (_seenLock)
        {
            var fp = Fingerprint(sub);
            var attempts = Seen().TryGetValue(sub, out var old) && old.StartsWith(Failed, StringComparison.Ordinal)
                           && ParseFailed(old) is var (n, last) && last == fp
                ? n + 1
                : 1;
            _seen![sub] = string.Create(CultureInfo.InvariantCulture, $"{Failed}{attempts}:{fp}");
            _dirty = true;
            if (attempts == MaxAttempts)
            {
                _logger.LogWarning("SemanticSubSync: the engine failed {Attempts} times on {Subtitle}: left alone until the file changes",
                    attempts, sub);
            }
        }
    }

    private void Flush()
    {
        lock (_seenLock)
        {
            FlushLocked();
        }
    }

    private void FlushLocked()
    {
        if (_dirty && _seen is not null)
        {
            WriteAtomic(SeenPath, JsonSerializer.Serialize(_seen));
            _dirty = false;
        }
    }

    private static void WriteAtomic(string path, string text)
    {
        Directory.CreateDirectory(EngineInstaller.DataDir);
        var tmp = path + ".tmp";
        File.WriteAllText(tmp, text);
        File.Move(tmp, path, overwrite: true);
    }

    private Dictionary<string, string> Seen()
    {
        if (_seen is not null)
        {
            return _seen;
        }

        try
        {
            _seen = File.Exists(SeenPath)
                ? JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(SeenPath)) ?? []
                : null;
        }
        catch (JsonException ex)
        {
            _logger.LogError("SemanticSubSync: {File} is unreadable ({Error}): kept as seen.json.bad; the subtitles of every library are recorded again as existing ones",
                SeenPath, ex.Message);
            File.Move(SeenPath, SeenPath + ".bad", overwrite: true);
        }

        if (_seen is null)
        {
            // without a record, every subtitle would look new: record the libraries again instead
            if (File.Exists(BaselinePath))
            {
                File.Delete(BaselinePath);
            }

            _baselined = [];
            _seen = [];
        }

        return _seen;
    }

    private HashSet<Guid> Baselined()
    {
        Seen();   // a lost seen.json drops the baseline too
        if (_baselined is not null)
        {
            return _baselined;
        }

        try
        {
            _baselined = File.Exists(BaselinePath)
                ? JsonSerializer.Deserialize<HashSet<Guid>>(File.ReadAllText(BaselinePath)) ?? []
                : [];
        }
        catch (JsonException ex)
        {
            _logger.LogError("SemanticSubSync: {File} is unreadable ({Error}): the libraries are recorded again", BaselinePath, ex.Message);
            _baselined = [];
        }

        return _baselined;
    }
}
