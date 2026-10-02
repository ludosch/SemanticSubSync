using System;
using System.Collections.Generic;
using System.Globalization;
using Jellyfin.Plugin.SemanticSubSync.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace Jellyfin.Plugin.SemanticSubSync;

/// <summary>Fixes the timing of external subtitles by matching their lines, by meaning, with a
/// subtitle embedded in the video. The work is done by the semantic-subsync engine (Python),
/// which the plugin installs in its own data folder and runs as a separate process.</summary>
public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Instance = this;
    }

    public override string Name => "SemanticSubSync";

    public override Guid Id => Guid.Parse("32462154-51a5-4097-ac51-e453bb9dd38d");

    public override string Description =>
        "Re-times downloaded subtitles on the subtitle embedded in the video, by matching lines on meaning.";

    public static Plugin? Instance { get; private set; }

    public IEnumerable<PluginPageInfo> GetPages()
    {
        return
        [
            new PluginPageInfo
            {
                Name = Name,
                EmbeddedResourcePath = string.Format(CultureInfo.InvariantCulture, "{0}.Configuration.configPage.html", GetType().Namespace)
            },
            new PluginPageInfo
            {
                Name = Web.MenuScriptInjection.Script,
                EmbeddedResourcePath = string.Format(CultureInfo.InvariantCulture, "{0}.Web.{1}", GetType().Namespace, Web.MenuScriptInjection.Script)
            }
        ];
    }
}
