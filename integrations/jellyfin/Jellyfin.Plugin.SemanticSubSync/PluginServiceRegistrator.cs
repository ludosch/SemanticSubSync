using System.Net.Http.Headers;
using Jellyfin.Plugin.SemanticSubSync.Engine;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Tasks;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.SemanticSubSync;

public sealed class PluginServiceRegistrator : IPluginServiceRegistrator
{
    public void RegisterServices(IServiceCollection serviceCollection, IServerApplicationHost applicationHost)
    {
        serviceCollection.AddHttpClient("SemanticSubSync", c =>
            c.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("Jellyfin-Plugin-SemanticSubSync", EngineInstaller.EngineVersion)));
        serviceCollection.AddSingleton<EngineInstaller>();
        serviceCollection.AddSingleton<EngineRunner>();
        serviceCollection.AddSingleton<SyncService>();
        serviceCollection.AddSingleton<IScheduledTask, SyncNewSubtitlesTask>();
        serviceCollection.AddHostedService<NewVideoListener>();
    }
}
