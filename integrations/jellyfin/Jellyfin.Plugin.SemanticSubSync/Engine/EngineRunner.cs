using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.MediaEncoding;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.SemanticSubSync.Engine;

/// <summary>The engine's decision on one subtitle: the JSON line `semantic-subsync-worker one` prints,
/// or "error" / "timeout" when there is none.</summary>
public sealed record EngineResult(string Status, bool FilesChanged, string Line)
{
    public static EngineResult Failed(string status) => new(status, false, string.Empty);
}

/// <summary>Runs `semantic-subsync-worker one VIDEO SUB`: the worker picks the embedded reference,
/// re-times, writes the files and keeps its state (state.db, semsync.log) in the plugin's data folder.</summary>
public sealed class EngineRunner
{
    private static readonly TimeSpan Timeout = TimeSpan.FromMinutes(30);

    // decisions that put nothing on disk; every other one may have written, restored or removed a file
    private static readonly string[] Untouched = ["skipped", "changed_during_run", "error", "timeout"];

    private readonly IMediaEncoder _mediaEncoder;
    private readonly ILogger<EngineRunner> _logger;

    public EngineRunner(IMediaEncoder mediaEncoder, ILogger<EngineRunner> logger)
    {
        _mediaEncoder = mediaEncoder;
        _logger = logger;
    }

    /// <summary>The worker keeps its state and the model in the plugin's data folder.</summary>
    internal static void SetEnvironment(ProcessStartInfo psi)
    {
        psi.Environment["SEMSYNC_DIR"] = Path.Combine(EngineInstaller.DataDir, "state");
        psi.Environment["HF_HOME"] = Path.Combine(EngineInstaller.DataDir, "models");
    }

    /// <summary>Throws only when `ct` is cancelled (Jellyfin is stopping); a timeout or a crash of the
    /// engine is a result ("timeout", "error").</summary>
    public async Task<EngineResult> RunAsync(string video, string subtitle, CancellationToken ct)
    {
        var psi = new ProcessStartInfo(EngineInstaller.WorkerPath)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.ArgumentList.Add("one");
        psi.ArgumentList.Add(video);
        psi.ArgumentList.Add(subtitle);
        SetEnvironment(psi);
        psi.Environment["SEMSYNC_OUTPUT"] = Plugin.Instance!.Configuration.Output;
        psi.Environment["SEMSYNC_KEEP_DOWNLOAD"] = Plugin.Instance.Configuration.KeepDownload;
        // the engine calls ffmpeg/ffprobe: use the ones Jellyfin ships with
        var ffmpegDir = Path.GetDirectoryName(_mediaEncoder.EncoderPath);
        if (!string.IsNullOrEmpty(ffmpegDir))
        {
            psi.Environment["PATH"] = ffmpegDir + Path.PathSeparator + (Environment.GetEnvironmentVariable("PATH") ?? string.Empty);
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(Timeout);
        Process p;
        try
        {
            p = Process.Start(psi) ?? throw new InvalidOperationException("the engine did not start");
        }
        catch (Exception ex) when (ex is Win32Exception or InvalidOperationException)
        {
            _logger.LogError(ex, "SemanticSubSync: the engine did not start on {Subtitle}", subtitle);
            return EngineResult.Failed("error");
        }

        using (p)
        {
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
                    throw;   // Jellyfin is stopping
                }

                _logger.LogError("SemanticSubSync: the engine took more than {Minutes} min on {Subtitle}: stopped",
                    Timeout.TotalMinutes, subtitle);
                return EngineResult.Failed("timeout");
            }

            var line = (await stdout.ConfigureAwait(false)).Split('\n', StringSplitOptions.RemoveEmptyEntries).LastOrDefault()?.Trim();
            if (p.ExitCode != 0 || string.IsNullOrEmpty(line) || !line.StartsWith('{'))
            {
                _logger.LogError("SemanticSubSync: engine failed on {Subtitle} (exit {Code}): {Error}",
                    subtitle, p.ExitCode, EngineInstaller.Tail(await stderr.ConfigureAwait(false)));
                return EngineResult.Failed("error");
            }

            string status;
            bool renamed;
            try
            {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                status = root.ValueKind == JsonValueKind.Object && root.TryGetProperty("status", out var s) && s.ValueKind == JsonValueKind.String
                    ? s.GetString()!
                    : "?";
                // an unchanged subtitle whose kept download was renamed (new name, other setting)
                renamed = root.ValueKind == JsonValueKind.Object && root.TryGetProperty("renamed", out _);
                if (status == "unchanged" && root.TryGetProperty("last", out var last) && last.ValueKind == JsonValueKind.String)
                {
                    status += ":" + last.GetString();   // e.g. "unchanged:corrected", the decision it keeps
                }
            }
            catch (JsonException ex)
            {
                _logger.LogError("SemanticSubSync: unreadable engine output on {Subtitle}: {Error}: {Line}",
                    subtitle, ex.Message, EngineInstaller.Tail(line));
                return EngineResult.Failed("error");
            }

            var changed = renamed || (!status.StartsWith("unchanged", StringComparison.Ordinal) && !Untouched.Contains(status));
            return new EngineResult(status, changed, line);
        }
    }
}
