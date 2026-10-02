using System;
using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.SemanticSubSync.Configuration;

/// <summary>Plugin settings, edited on the plugin page of the dashboard.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Gets or sets the ids of the libraries whose subtitles are processed. Empty by default:
    /// nothing is processed until a library is chosen.</summary>
    public string[] Libraries { get; set; } = [];

    /// <summary>Gets or sets a value indicating whether the subtitles already in a library when it is
    /// chosen are processed too. Off: they are only recorded, and only subtitles added or changed
    /// afterwards are processed (a large library can take hours on a small server).</summary>
    public bool ProcessExisting { get; set; }

    /// <summary>Gets or sets where a correction goes: "side" (written as .resync.default., played by default,
    /// the download untouched) or "replace" (the correction takes the subtitle's name, the download is
    /// kept as .replaced.).</summary>
    public string OutputMode { get; set; } = "side";

    /// <summary>Gets or sets an optional engine to install instead of the release this plugin was built
    /// for: a wheel path or URL (for testing a development build). Empty = the official release.</summary>
    public string EngineSource { get; set; } = string.Empty;

    /// <summary>Whether the library with this id was chosen.</summary>
    public bool IsChosen(Guid libraryId) =>
        Array.Exists(Libraries, id => Guid.TryParse(id, out var g) && g == libraryId);
}
