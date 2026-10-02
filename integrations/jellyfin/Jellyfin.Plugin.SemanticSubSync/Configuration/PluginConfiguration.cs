using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.SemanticSubSync.Configuration;

/// <summary>Plugin settings, edited on the plugin page of the dashboard.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Gets or sets a value indicating whether subtitles are processed at all.</summary>
    public bool Enabled { get; set; } = true;

    /// <summary>Gets or sets where a correction goes: "replace" (the correction takes the subtitle's
    /// name, the download is kept as .replaced.) or "side" (written as .resync., download untouched).</summary>
    public string OutputMode { get; set; } = "replace";

    /// <summary>Gets or sets a value indicating whether the subtitles already in the library when the
    /// plugin first runs are processed too. Off: the first run only records them, and only subtitles
    /// added or changed afterwards are processed (a large library can take hours on a small server).</summary>
    public bool ProcessExisting { get; set; }

    /// <summary>Gets or sets an optional engine to install instead of the release this plugin was built
    /// for: a wheel path or URL (for testing a development build). Empty = the official release.</summary>
    public string EngineSource { get; set; } = string.Empty;
}
