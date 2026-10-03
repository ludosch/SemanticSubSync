using System;
using System.Text.Json.Serialization;
using System.Xml.Serialization;
using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.SemanticSubSync.Configuration;

/// <summary>Plugin settings, edited on the plugin page of the dashboard.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Gets or sets the ids of the libraries left out of the automatic processing (new videos
    /// and the scheduled task; the menu entry works everywhere). Empty by default: new subtitles are
    /// processed in every library, including libraries created later.</summary>
    public string[] ExcludedLibraries { get; set; } = [];

    /// <summary>Gets or sets a value indicating whether the subtitles already in a library when the plugin
    /// first sees it (its installation, or the library's creation) are processed too. Off: they are only
    /// recorded, and only subtitles added or changed afterwards are processed (a large library can take
    /// hours on a small server). On: the scheduled task processes them as well.</summary>
    public bool ProcessExisting { get; set; }

    /// <summary>Gets or sets where a correction goes: "replace" (the correction takes the subtitle's name,
    /// the download is kept as .replaced.) or "side" (written as .resync.default., played by default, the
    /// download untouched). Any other value counts as "replace", see <see cref="Output"/>.</summary>
    public string OutputMode { get; set; } = "replace";

    /// <summary>Gets or sets an optional engine to install instead of the release this plugin was built
    /// for: a wheel path or URL (for testing a development build). Empty = the official release. The
    /// working engine is kept until this one is installed and has loaded its model.</summary>
    public string EngineSource { get; set; } = string.Empty;

    /// <summary>Gets <see cref="OutputMode"/> as the engine knows it: "side" or "replace".</summary>
    [XmlIgnore]
    [JsonIgnore]
    public string Output => string.Equals(OutputMode?.Trim(), "side", StringComparison.OrdinalIgnoreCase) ? "side" : "replace";

    /// <summary>Whether new subtitles of the library with this id are processed automatically.</summary>
    public bool IsChosen(Guid libraryId) =>
        !Array.Exists(ExcludedLibraries, id => Guid.TryParse(id, out var g) && g == libraryId);
}
