using System;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Net.Http.Headers;

namespace Jellyfin.Plugin.SemanticSubSync.Web;

/// <summary>Adds the plugin's script (the "Sync subtitles" menu entry) to the web client's
/// index.html as it is served. Nothing is written to Jellyfin's files.</summary>
public sealed class MenuScriptInjection
{
    private const string ScriptName = "semanticsubsync-menu.js";

    private static readonly string[] Conditional =
        [HeaderNames.IfNoneMatch, HeaderNames.IfModifiedSince, HeaderNames.Range, HeaderNames.IfRange];

    private readonly RequestDelegate _next;

    public MenuScriptInjection(RequestDelegate next)
    {
        _next = next;
    }

    public static string Script => ScriptName;

    public async Task InvokeAsync(HttpContext context)
    {
        var path = context.Request.Path.Value ?? string.Empty;
        var isIndex = path.EndsWith("/web/", StringComparison.OrdinalIgnoreCase)
                      || path.EndsWith("/web/index.html", StringComparison.OrdinalIgnoreCase);
        if (!HttpMethods.IsGet(context.Request.Method) || !isIndex)
        {
            await _next(context).ConfigureAwait(false);
            return;
        }

        // ask for the plain, full page so it can be edited
        context.Request.Headers.Remove(HeaderNames.AcceptEncoding);
        foreach (var h in Conditional)
        {
            context.Request.Headers.Remove(h);
        }

        var original = context.Response.Body;
        using var buffer = new MemoryStream();
        context.Response.Body = buffer;
        try
        {
            await _next(context).ConfigureAwait(false);
        }
        finally
        {
            context.Response.Body = original;
        }

        var html = context.Response.StatusCode == StatusCodes.Status200OK && context.Response.Headers.ContentEncoding.Count == 0
            ? Encoding.UTF8.GetString(buffer.GetBuffer(), 0, (int)buffer.Length)
            : null;
        var head = html?.IndexOf("</head>", StringComparison.OrdinalIgnoreCase) ?? -1;
        if (html is null || head < 0 || html.Contains(ScriptName, StringComparison.Ordinal))
        {
            buffer.Position = 0;
            await buffer.CopyToAsync(original, context.RequestAborted).ConfigureAwait(false);
            return;
        }

        var basePath = path.EndsWith("/index.html", StringComparison.OrdinalIgnoreCase) ? path[..^"/index.html".Length] : path.TrimEnd('/');
        var version = typeof(MenuScriptInjection).Assembly.GetName().Version;
        var tag = $"<script src=\"{basePath}/configurationpage?name={ScriptName}&amp;v={version}\" defer></script>";
        var bytes = Encoding.UTF8.GetBytes(html.Insert(head, tag));
        context.Response.ContentLength = bytes.Length;
        context.Response.Headers.Remove(HeaderNames.ETag);
        context.Response.Headers.Remove(HeaderNames.LastModified);
        context.Response.Headers.CacheControl = "no-cache";
        await original.WriteAsync(bytes, context.RequestAborted).ConfigureAwait(false);
    }
}

/// <summary>Puts <see cref="MenuScriptInjection"/> in front of Jellyfin's own pipeline.</summary>
public sealed class MenuScriptStartupFilter : IStartupFilter
{
    public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
    {
        app.UseMiddleware<MenuScriptInjection>();
        next(app);
    };
}
