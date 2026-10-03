using System;
using System.Diagnostics;
using System.Formats.Tar;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync.Engine;

/// <summary>Where the engine stands, shown on the plugin page.</summary>
/// <param name="State">not_installed, installing, ready or failed.</param>
/// <param name="Message">Why it failed, or what it installs.</param>
/// <param name="Since">When this state began (UTC).</param>
public sealed record EngineStatus(string State, string? Message, DateTime Since);

/// <summary>Installs the engine in the plugin's data folder: the uv binary (pinned, checksum
/// verified), a standalone Python fetched by uv, semantic-subsync[static] in a venv, then the
/// sentence model (`worker prepare`, pinned and checksum verified by the engine). The official
/// release is installed with the dependency versions published with it (its constraints file);
/// an engine source set by hand is installed with the constraints file found next to it, if any.
/// Each installation is built in engine.new and takes the place of engine/ only once its model is
/// ready, while no subtitle is being processed; on failure the working engine stays. uv is deleted
/// once done. Nothing is installed system-wide; deleting the data folder removes everything. One
/// installation at a time: a sync that needs the engine waits for the one in progress.</summary>
public sealed class EngineInstaller : IDisposable
{
    /// <summary>The semantic-subsync release this plugin is built for.</summary>
    public const string EngineVersion = "0.12.0";

    private const string UvVersion = "0.12.17";
    private const string PythonVersion = "3.12";
    private const string Releases = "https://github.com/ludosch/SemanticSubSync/releases/download";

    private static readonly TimeSpan UvTimeout = TimeSpan.FromMinutes(30);
    private static readonly TimeSpan PrepareTimeout = TimeSpan.FromMinutes(60);
    private static readonly TimeSpan RetryAfter = TimeSpan.FromHours(1);

    private readonly IHttpClientFactory _http;
    private readonly ILogger<EngineInstaller> _logger;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly SemaphoreSlim _runLock = new(1, 1);
    private EngineStatus? _status;
    private (string Source, DateTime At)? _failed;

    public EngineInstaller(IHttpClientFactory http, ILogger<EngineInstaller> logger)
    {
        _http = http;
        _logger = logger;
    }

    public void Dispose()
    {
        _gate.Dispose();
        _runLock.Dispose();
    }

    public static string DataDir => Plugin.Instance!.DataFolderPath;

    public static string EngineDir => Path.Combine(DataDir, "engine");

    public static string VenvDir => Path.Combine(EngineDir, "venv");

    public static string WorkerPath => Worker(EngineDir);

    /// <summary>Held while the engine runs on a subtitle: one at a time, and never while the engine
    /// is being replaced.</summary>
    internal SemaphoreSlim RunLock => _runLock;

    private static string NewDir => Path.Combine(DataDir, "engine.new");

    private static string OldDir => Path.Combine(DataDir, "engine.old");

    // outside engine/: the venv points to it by absolute path, and engine.new becomes engine/
    private static string PythonDir => Path.Combine(DataDir, "python");

    private static string MarkerPath => Path.Combine(EngineDir, "installed.txt");

    /// <summary>The current state; before any attempt, ready when an installation is on disk.</summary>
    public EngineStatus Status => _status ?? new EngineStatus(HasEngine() ? "ready" : "not_installed", null, DateTime.UtcNow);

    private static string? Override => Plugin.Instance?.Configuration.EngineSource?.Trim() is { Length: > 0 } s ? s : null;

    /// <summary>What gets installed: the configured override, else the official wheel of EngineVersion.</summary>
    public static string Source =>
        Override ?? $"{Releases}/v{EngineVersion}/semantic_subsync-{EngineVersion}-py3-none-any.whl";

    /// <summary>Installs or updates the engine when needed. Returns whether an engine can run: after a
    /// failed update, the previous one (logged). A source that failed is tried again an hour later at
    /// the earliest.</summary>
    public async Task<bool> EnsureAsync(CancellationToken ct)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            Recover();
            if (IsInstalled())
            {
                _status = _status is { State: "ready" } ? _status : new EngineStatus("ready", null, DateTime.UtcNow);
                return true;
            }

            var usable = HasEngine();
            if (!OperatingSystem.IsLinux())
            {
                _logger.LogError("SemanticSubSync: the engine can only be installed on Linux for now");
                _status = new EngineStatus("failed", "the engine can only be installed on Linux for now", DateTime.UtcNow);
                return usable;
            }

            var source = Source;
            if (_failed is { } f && f.Source == source && DateTime.UtcNow - f.At < RetryAfter)
            {
                return usable;
            }

            _status = new EngineStatus("installing", source, DateTime.UtcNow);
            try
            {
                await InstallAsync(source, ct).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _logger.LogError(ex, "SemanticSubSync: engine installation failed{Kept}",
                    usable ? "; the previous engine is still used" : string.Empty);
                _failed = (source, DateTime.UtcNow);
                _status = new EngineStatus("failed", ex.Message + (usable ? " (the previous engine is still used)" : string.Empty), DateTime.UtcNow);
                TryDelete(NewDir);
                return usable;
            }

            _failed = null;
            _logger.LogInformation("SemanticSubSync: engine ready");
            _status = new EngineStatus("ready", null, DateTime.UtcNow);
            return true;
        }
        finally
        {
            if (_status is { State: "installing" })   // cancelled: Jellyfin is stopping
            {
                _status = null;
            }

            _gate.Release();
        }
    }

    private static string Worker(string engineDir) => Path.Combine(engineDir, "venv", "bin", "semantic-subsync-worker");

    private static bool HasEngine() => File.Exists(WorkerPath) && File.Exists(MarkerPath);

    private static bool IsInstalled() => HasEngine() && File.ReadAllText(MarkerPath).Trim() == Source;

    /// <summary>Puts back an engine left aside by a swap that did not finish (Jellyfin stopped), and
    /// removes what an interrupted installation left.</summary>
    private void Recover()
    {
        if (!Directory.Exists(EngineDir) && Directory.Exists(OldDir))
        {
            _logger.LogWarning("SemanticSubSync: restoring the previous engine after an interrupted update");
            Directory.Move(OldDir, EngineDir);
        }

        TryDelete(OldDir);
        TryDelete(NewDir);
    }

    private async Task InstallAsync(string source, CancellationToken ct)
    {
        Directory.CreateDirectory(NewDir);
        var uv = Path.Combine(NewDir, "uv");
        await InstallUvAsync(uv, ct).ConfigureAwait(false);
        var constraints = await ConstraintsAsync(source, Path.Combine(NewDir, "constraints.txt"), ct).ConfigureAwait(false);

        _logger.LogInformation("SemanticSubSync: installing the engine from {Source}", source);
        var venv = Path.Combine(NewDir, "venv");
        // relocatable: its scripts keep working once engine.new is renamed engine
        await RunUvAsync(uv, ["venv", "--relocatable", "--python", PythonVersion, "--python-preference", "only-managed", venv], ct).ConfigureAwait(false);
        string[] pin = constraints is null ? [] : ["-c", constraints];
        await RunUvAsync(uv, ["pip", "install", "--python", Path.Combine(venv, "bin", "python"), .. pin, $"semantic-subsync[static] @ {source}"], ct).ConfigureAwait(false);
        TryDelete(Path.Combine(NewDir, "uv-cache"));   // packages are copied into the venv
        File.Delete(uv);   // not needed to run; fetched again for an update
        _logger.LogInformation("SemanticSubSync: engine installed, preparing the sentence model");
        await PrepareAsync(Worker(NewDir), ct).ConfigureAwait(false);
        await File.WriteAllTextAsync(Path.Combine(NewDir, "installed.txt"), source, ct).ConfigureAwait(false);

        await _runLock.WaitAsync(ct).ConfigureAwait(false);   // no worker runs from engine/ meanwhile
        try
        {
            if (Directory.Exists(EngineDir))
            {
                Directory.Move(EngineDir, OldDir);
            }

            Directory.Move(NewDir, EngineDir);
        }
        finally
        {
            _runLock.Release();
        }

        TryDelete(OldDir);
    }

    /// <summary>The pinned dependency versions to install with: those published with the official
    /// release (required), or the constraints file next to a wheel set by hand (optional). Null when
    /// there is none.</summary>
    private async Task<string?> ConstraintsAsync(string source, string target, CancellationToken ct)
    {
        if (Override is null)
        {
            var url = $"{Releases}/v{EngineVersion}/semantic-subsync-{EngineVersion}-constraints.txt";
            var text = await _http.CreateClient("SemanticSubSync").GetStringAsync(url, ct).ConfigureAwait(false);
            await File.WriteAllTextAsync(target, text, ct).ConfigureAwait(false);
            return target;
        }

        var wheel = Regex.Match(source, @"semantic_subsync-([^-/\\]+)-[^/\\]*\.whl$", RegexOptions.CultureInvariant);
        if (!wheel.Success)
        {
            _logger.LogInformation("SemanticSubSync: engine source set by hand, dependencies not pinned");
            return null;
        }

        var sibling = source[..wheel.Index] + $"semantic-subsync-{wheel.Groups[1].Value}-constraints.txt";
        try
        {
            if (sibling.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || sibling.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                var text = await _http.CreateClient("SemanticSubSync").GetStringAsync(sibling, ct).ConfigureAwait(false);
                await File.WriteAllTextAsync(target, text, ct).ConfigureAwait(false);
                return target;
            }

            var path = sibling.StartsWith("file:", StringComparison.OrdinalIgnoreCase) ? new Uri(sibling).LocalPath : sibling;
            if (File.Exists(path))
            {
                File.Copy(path, target, overwrite: true);
                return target;
            }
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or UriFormatException)
        {
            _logger.LogDebug(ex, "SemanticSubSync: no constraints file at {Url}", sibling);
        }

        _logger.LogInformation("SemanticSubSync: no {File} next to the engine source, dependencies not pinned",
            Path.GetFileName(sibling));
        return null;
    }

    /// <summary>`worker prepare`: downloads and loads the model, so the first subtitle does not wait for it.</summary>
    private static async Task PrepareAsync(string worker, CancellationToken ct)
    {
        var psi = new ProcessStartInfo(worker) { RedirectStandardOutput = true, RedirectStandardError = true };
        psi.ArgumentList.Add("prepare");
        EngineRunner.SetEnvironment(psi);
        var (code, stdout, stderr) = await RunAsync(psi, "the sentence model preparation", PrepareTimeout, ct).ConfigureAwait(false);
        if (code != 0 || !stdout.Contains("\"ready\"", StringComparison.Ordinal))
        {
            throw new InvalidOperationException($"the sentence model could not be prepared ({code}): {Tail(stderr)}");
        }
    }

    private async Task InstallUvAsync(string uv, CancellationToken ct)
    {
        var (target, sha) = RuntimeInformation.OSArchitecture switch
        {
            Architecture.X64 => ("x86_64-unknown-linux-gnu", "fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63"),
            Architecture.Arm64 => ("aarch64-unknown-linux-gnu", "d636d1b678e9e7f367ecb22b46bd1cabbed234d6bc3b4d96365d2b507f72f86c"),
            var a => throw new PlatformNotSupportedException($"no engine build for {a}"),
        };
        var url = $"https://github.com/astral-sh/uv/releases/download/{UvVersion}/uv-{target}.tar.gz";
        _logger.LogInformation("SemanticSubSync: downloading {Url}", url);
        var bytes = await _http.CreateClient("SemanticSubSync").GetByteArrayAsync(url, ct).ConfigureAwait(false);
        if (!Convert.ToHexStringLower(SHA256.HashData(bytes)).Equals(sha, StringComparison.Ordinal))
        {
            throw new InvalidDataException("uv download does not match its checksum");
        }

        using var gz = new GZipStream(new MemoryStream(bytes), CompressionMode.Decompress);
        using var tar = new TarReader(gz);
        while (await tar.GetNextEntryAsync(cancellationToken: ct).ConfigureAwait(false) is { } entry)
        {
            if (entry.EntryType == TarEntryType.RegularFile && Path.GetFileName(entry.Name) == "uv")
            {
                await entry.ExtractToFileAsync(uv, overwrite: true, ct).ConfigureAwait(false);
                if (!OperatingSystem.IsWindows())
                {
                    File.SetUnixFileMode(uv, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute
                                             | UnixFileMode.GroupRead | UnixFileMode.GroupExecute);
                }

                return;
            }
        }

        throw new InvalidDataException("uv binary not found in the archive");
    }

    private static async Task RunUvAsync(string uv, string[] args, CancellationToken ct)
    {
        var psi = new ProcessStartInfo(uv) { RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in args)
        {
            psi.ArgumentList.Add(a);
        }

        // everything uv downloads or caches stays in the plugin's data folder
        psi.Environment["UV_PYTHON_INSTALL_DIR"] = PythonDir;
        psi.Environment["UV_CACHE_DIR"] = Path.Combine(NewDir, "uv-cache");
        psi.Environment["UV_NO_CONFIG"] = "1";
        psi.Environment["UV_LINK_MODE"] = "copy";   // the cache is deleted after the install
        var (code, _, stderr) = await RunAsync(psi, $"uv {args[0]}", UvTimeout, ct).ConfigureAwait(false);
        if (code != 0)
        {
            throw new InvalidOperationException($"uv {args[0]} failed ({code}): {Tail(stderr)}");
        }
    }

    /// <summary>Runs a process to its end. Past `limit`, or when `ct` is cancelled, the process and its
    /// children are killed: a TimeoutException for the former, the cancellation for the latter.</summary>
    private static async Task<(int Code, string Stdout, string Stderr)> RunAsync(ProcessStartInfo psi, string what, TimeSpan limit, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(limit);
        using var p = Process.Start(psi) ?? throw new InvalidOperationException($"{what} did not start");
        var stdout = p.StandardOutput.ReadToEndAsync(timeout.Token);
        var stderr = p.StandardError.ReadToEndAsync(timeout.Token);
        try
        {
            await p.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            p.Kill(entireProcessTree: true);
            if (ct.IsCancellationRequested)
            {
                throw;
            }

            throw new TimeoutException($"{what} took more than {limit.TotalMinutes} min: stopped");
        }

        return (p.ExitCode, await stdout.ConfigureAwait(false), await stderr.ConfigureAwait(false));
    }

    private static void TryDelete(string dir)
    {
        try
        {
            if (Directory.Exists(dir))
            {
                Directory.Delete(dir, recursive: true);
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            // left for the next attempt
        }
    }

    internal static string Tail(string s, int n = 800) => s.Length <= n ? s.Trim() : s[^n..].Trim();
}
