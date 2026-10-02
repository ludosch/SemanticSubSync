using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.MediaEncoding;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync.Engine;

/// <summary>The engine's decision on one subtitle: the JSON line `semantic-subsync-worker one` prints.</summary>
public sealed record EngineResult(string Status, bool FilesChanged, string Line);

/// <summary>Runs `semantic-subsync-worker one VIDEO SUB`: the worker picks the embedded reference,
/// re-times, writes the files and keeps its state (state.db, semsync.log) in the plugin's data folder.</summary>
public sealed class EngineRunner
{
    private static readonly TimeSpan Timeout = TimeSpan.FromMinutes(30);

    private readonly IMediaEncoder _mediaEncoder;
    private readonly ILogger<EngineRunner> _logger;

    public EngineRunner(IMediaEncoder mediaEncoder, ILogger<EngineRunner> logger)
    {
        _mediaEncoder = mediaEncoder;
        _logger = logger;
    }

    public async Task<EngineResult?> RunAsync(string video, string subtitle, CancellationToken ct)
    {
        var config = Plugin.Instance!.Configuration;
        var psi = new ProcessStartInfo(EngineInstaller.WorkerPath)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.ArgumentList.Add("one");
        psi.ArgumentList.Add(video);
        psi.ArgumentList.Add(subtitle);
        psi.Environment["SEMSYNC_DIR"] = Path.Combine(EngineInstaller.DataDir, "state");
        psi.Environment["SEMSYNC_OUTPUT"] = config.OutputMode == "side" ? "side" : "replace";
        psi.Environment["HF_HOME"] = Path.Combine(EngineInstaller.DataDir, "models");
        psi.Environment["HF_HUB_DISABLE_PROGRESS_BARS"] = "1";
        // the engine calls ffmpeg/ffprobe: use the ones Jellyfin ships with
        var ffmpegDir = Path.GetDirectoryName(_mediaEncoder.EncoderPath);
        if (!string.IsNullOrEmpty(ffmpegDir))
        {
            psi.Environment["PATH"] = ffmpegDir + Path.PathSeparator + (Environment.GetEnvironmentVariable("PATH") ?? string.Empty);
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(Timeout);
        using var p = Process.Start(psi) ?? throw new InvalidOperationException("the engine did not start");
        var stdout = p.StandardOutput.ReadToEndAsync(timeout.Token);
        var stderr = p.StandardError.ReadToEndAsync(timeout.Token);
        try
        {
            await p.WaitForExitAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            p.Kill(entireProcessTree: true);
            throw;
        }

        var line = (await stdout.ConfigureAwait(false)).Split('\n', StringSplitOptions.RemoveEmptyEntries).LastOrDefault()?.Trim();
        if (p.ExitCode != 0 || string.IsNullOrEmpty(line) || !line.StartsWith('{'))
        {
            _logger.LogError("SemanticSubSync: engine failed on {Subtitle} (exit {Code}): {Error}",
                subtitle, p.ExitCode, EngineInstaller.Tail(await stderr.ConfigureAwait(false)));
            return null;
        }

        using var doc = JsonDocument.Parse(line);
        var root = doc.RootElement;
        var status = root.TryGetProperty("status", out var s) ? s.GetString() ?? "?" : "?";
        if (status == "unchanged" && root.TryGetProperty("last", out var last) && last.ValueKind == JsonValueKind.String)
        {
            status += ":" + last.GetString();   // e.g. "unchanged:corrected", the decision it keeps
        }
        // any decision may have written, restored or removed a file; only these two touch nothing
        var changed = !status.StartsWith("unchanged", StringComparison.Ordinal) && status != "skipped";
        return new EngineResult(status, changed, line);
    }
}
