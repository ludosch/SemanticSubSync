using System;
using System.Diagnostics;
using System.Formats.Tar;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync.Engine;

/// <summary>Installs the engine in the plugin's data folder: the uv binary (pinned, checksum
/// verified), a standalone Python fetched by uv, and semantic-subsync[static] in a venv.
/// Nothing is installed system-wide; deleting the data folder removes everything.</summary>
public sealed class EngineInstaller : IDisposable
{
    /// <summary>The semantic-subsync release this plugin is built for.</summary>
    public const string EngineVersion = "0.12.0";

    private const string UvVersion = "0.12.17";
    private const string PythonVersion = "3.12";

    private readonly IHttpClientFactory _http;
    private readonly ILogger<EngineInstaller> _logger;
    private readonly SemaphoreSlim _gate = new(1, 1);

    public EngineInstaller(IHttpClientFactory http, ILogger<EngineInstaller> logger)
    {
        _http = http;
        _logger = logger;
    }

    public void Dispose() => _gate.Dispose();

    public static string DataDir => Plugin.Instance!.DataFolderPath;

    public static string EngineDir => Path.Combine(DataDir, "engine");

    public static string VenvDir => Path.Combine(EngineDir, "venv");

    public static string WorkerPath => Path.Combine(VenvDir, "bin", "semantic-subsync-worker");

    private static string UvPath => Path.Combine(EngineDir, "uv");

    private static string MarkerPath => Path.Combine(EngineDir, "installed.txt");

    /// <summary>What gets installed: the configured override, else the official wheel of EngineVersion.</summary>
    public static string Source
    {
        get
        {
            var over = Plugin.Instance?.Configuration.EngineSource?.Trim();
            return string.IsNullOrEmpty(over)
                ? $"https://github.com/ludosch/SemanticSubSync/releases/download/v{EngineVersion}/semantic_subsync-{EngineVersion}-py3-none-any.whl"
                : over;
        }
    }

    /// <summary>Installs or updates the engine when needed. Returns false (and logs why) on failure.</summary>
    public async Task<bool> EnsureAsync(CancellationToken ct)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (File.Exists(WorkerPath) && File.Exists(MarkerPath)
                && (await File.ReadAllTextAsync(MarkerPath, ct).ConfigureAwait(false)).Trim() == Source)
            {
                return true;
            }

            if (!OperatingSystem.IsLinux())
            {
                _logger.LogError("SemanticSubSync: the engine can only be installed on Linux for now");
                return false;
            }

            Directory.CreateDirectory(EngineDir);
            if (!File.Exists(UvPath))
            {
                await InstallUvAsync(ct).ConfigureAwait(false);
            }

            _logger.LogInformation("SemanticSubSync: installing the engine from {Source}", Source);
            await RunUvAsync(["venv", "--clear", "--python", PythonVersion, "--python-preference", "only-managed", VenvDir], ct).ConfigureAwait(false);
            await RunUvAsync(["pip", "install", "--python", Path.Combine(VenvDir, "bin", "python"), $"semantic-subsync[static] @ {Source}"], ct).ConfigureAwait(false);
            await File.WriteAllTextAsync(MarkerPath, Source, ct).ConfigureAwait(false);
            Directory.Delete(Path.Combine(EngineDir, "uv-cache"), recursive: true);   // packages are copied into the venv
            _logger.LogInformation("SemanticSubSync: engine installed");
            return true;
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            _logger.LogError(ex, "SemanticSubSync: engine installation failed");
            return false;
        }
        finally
        {
            _gate.Release();
        }
    }

    private async Task InstallUvAsync(CancellationToken ct)
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
                await entry.ExtractToFileAsync(UvPath, overwrite: true, ct).ConfigureAwait(false);
                if (!OperatingSystem.IsWindows())
                {
                    File.SetUnixFileMode(UvPath, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute
                                             | UnixFileMode.GroupRead | UnixFileMode.GroupExecute);
                }

                return;
            }
        }

        throw new InvalidDataException("uv binary not found in the archive");
    }

    private async Task RunUvAsync(string[] args, CancellationToken ct)
    {
        var psi = new ProcessStartInfo(UvPath) { RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var a in args)
        {
            psi.ArgumentList.Add(a);
        }

        // everything uv downloads or caches stays in the plugin's data folder
        psi.Environment["UV_PYTHON_INSTALL_DIR"] = Path.Combine(EngineDir, "python");
        psi.Environment["UV_CACHE_DIR"] = Path.Combine(EngineDir, "uv-cache");
        psi.Environment["UV_NO_CONFIG"] = "1";
        psi.Environment["UV_LINK_MODE"] = "copy";   // the cache is deleted after the install
        using var p = Process.Start(psi) ?? throw new InvalidOperationException("uv did not start");
        var stdout = p.StandardOutput.ReadToEndAsync(ct);
        var stderr = p.StandardError.ReadToEndAsync(ct);
        await p.WaitForExitAsync(ct).ConfigureAwait(false);
        if (p.ExitCode != 0)
        {
            throw new InvalidOperationException($"uv {args[0]} failed ({p.ExitCode}): {Tail(await stderr.ConfigureAwait(false))}");
        }

        _ = await stdout.ConfigureAwait(false);
    }

    internal static string Tail(string s, int n = 800) => s.Length <= n ? s.Trim() : s[^n..].Trim();
}
