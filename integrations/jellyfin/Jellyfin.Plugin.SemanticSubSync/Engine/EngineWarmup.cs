using System;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Extensions.Hosting;

namespace Jellyfin.Plugin.SemanticSubSync.Engine;

/// <summary>Installs the engine and its model in the background once Jellyfin has started (the restart
/// that follows the plugin's installation), so the first sync does not wait for the download. After a
/// failure it tries again every hour; a sync that needs the engine also tries, and waits for an
/// installation in progress rather than starting a second one.</summary>
public sealed class EngineWarmup : BackgroundService
{
    private static readonly TimeSpan AfterStart = TimeSpan.FromMinutes(2);
    private static readonly TimeSpan Retry = TimeSpan.FromHours(1);

    private readonly EngineInstaller _installer;

    public EngineWarmup(EngineInstaller installer) => _installer = installer;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        try
        {
            await Task.Delay(AfterStart, stoppingToken).ConfigureAwait(false);   // Jellyfin finishes starting first
            while (!await _installer.EnsureAsync(stoppingToken).ConfigureAwait(false))
            {
                await Task.Delay(Retry, stoppingToken).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
            // Jellyfin is stopping
        }
    }
}
