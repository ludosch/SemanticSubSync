// SemanticSubSync: adds "Sync subtitles" to the "..." menu of movies and episodes (administrators).
// The menu is Jellyfin's action sheet, shared with other pickers: the entry is added only to a sheet
// that opens right after a click on an item's own menu button (card, list row, or the details
// page's "..." button) and that holds the item menu's own entries. Written against jellyfin-web 12.1.
(function () {
    'use strict';
    if (window.semanticSubSyncMenu) {
        return;
    }
    window.semanticSubSyncMenu = true;

    var TYPES = ['Movie', 'Episode'];
    // entries only the item menu has (an administrator always gets some of them)
    var ITEM_MENU = ['refresh', 'edit', 'editimages', 'editsubtitles', 'identify', 'moremediainfo'];
    var WINDOW_MS = 3000;   // the menu opens after fetching the item: allow for a slow connection
    var LABELS = {
        corrected: 'corrected',
        in_sync: 'already in sync',
        unsure: 'left alone (unsure)',
        no_reference: 'no embedded subtitle to compare with',
        redundant: 'the video already has this subtitle',
        unchanged: 'unchanged since last time',
        skipped: 'skipped',
        changed_during_run: 'the file changed meanwhile: try again',
        read_only: 'cannot write in the folder (read-only)',
        error: 'error (see the Jellyfin log)',
        timeout: 'took too long: stopped (see the Jellyfin log)',
        cancelled: 'stopped: Jellyfin is shutting down',
        no_subtitle: 'no external .srt subtitle',
        engine_unavailable: 'the engine could not be installed (see the Jellyfin log)'
    };
    var trigger = null;   // { id, at }: the item whose menu button was just clicked
    var warned = {};

    // jellyfin-web is not an API: say once when its markup no longer matches, rather than nothing
    function warnOnce(what) {
        if (!warned[what]) {
            warned[what] = true;
            console.warn('SemanticSubSync: ' + what + ': the "Sync subtitles" menu entry may be missing (written for jellyfin-web 12.1)');
        }
    }

    function api(method, path) {
        return ApiClient.ajax({ type: method, url: ApiClient.getUrl(path), dataType: 'json' });
    }

    function toast(text, sticky) {
        var old = document.querySelector('.sssToast');
        if (old) {
            old.remove();
        }
        var div = document.createElement('div');
        div.className = 'sssToast';
        div.style.cssText = 'position:fixed;left:50%;bottom:5em;transform:translateX(-50%);z-index:99999;' +
            'background:#202020;color:#fff;padding:.9em 1.4em;border-radius:.4em;max-width:90vw;' +
            'box-shadow:0 2px 12px rgba(0,0,0,.5);white-space:pre-line;font-size:.95em';
        div.textContent = text;
        div.addEventListener('click', function () { div.remove(); });
        document.body.appendChild(div);
        if (!sticky) {
            setTimeout(function () { div.remove(); }, 12000);
        }
    }

    function itemIdFromAddress() {
        var hash = window.location.hash || '';
        var q = hash.indexOf('?');
        var id = q >= 0 ? new URLSearchParams(hash.substring(q + 1)).get('id') : null;
        return id || new URLSearchParams(window.location.search || '').get('id');
    }

    function report(name, job) {
        var lines = (job.Results || []).map(function (r) {
            var parts = r.Status.split(':');   // "unchanged:corrected" = nothing new since that decision
            var what = parts.length > 1
                ? 'unchanged since last time (' + (LABELS[parts[1]] || parts[1]) + ')'
                : LABELS[r.Status] || r.Status;
            return r.Subtitle ? r.Subtitle + ': ' + what : what;
        });
        toast(name + '\n' + lines.join('\n'));
    }

    function poll(name, jobId) {
        api('GET', 'SemanticSubSync/Jobs/' + jobId).then(function (job) {
            if (job.Done) {
                report(name, job);
            } else {
                setTimeout(function () { poll(name, jobId); }, 3000);
            }
        }, function () {
            toast(name + '\nLost track of the sync: see the Jellyfin log.');
        });
    }

    function sync(item) {
        toast(item.Name + '\nSyncing subtitles… (the first time, the engine is installed: a few minutes)', true);
        api('POST', 'SemanticSubSync/Items/' + item.Id + '/Sync').then(function (job) {
            poll(item.Name, job.Id);
        }, function () {
            toast(item.Name + '\nCould not start the sync.');
        });
    }

    function addEntry(sheet, item) {
        var list = sheet.querySelector('.actionSheetScroller');
        if (!list) {
            warnOnce('no .actionSheetScroller in the item menu');
            return;
        }
        if (list.querySelector('.sssSyncEntry')) {
            return;
        }
        // An actionSheetMenuItem without data-id: the sheet's own click handler closes the sheet as
        // for a cancel (no command runs), the same way it closes for its own entries.
        var button = document.createElement('button');
        button.setAttribute('is', 'emby-button');
        button.type = 'button';
        button.className = 'listItem listItem-button actionSheetMenuItem sssSyncEntry';
        button.innerHTML =
            '<span class="actionsheetMenuItemIcon listItemIcon listItemIcon-transparent material-icons subtitles" aria-hidden="true"></span>' +
            '<div class="listItemBody actionsheetListItemBody"><div class="listItemBodyText actionSheetItemText">Sync subtitles</div></div>';
        button.addEventListener('click', function () {
            sync(item);
        });
        list.appendChild(button);
    }

    function isItemMenu(sheet) {
        return ITEM_MENU.some(function (id) {
            return sheet.querySelector('.actionSheetMenuItem[data-id="' + id + '"]');
        });
    }

    function onSheet(sheet) {
        var t = trigger;
        trigger = null;   // one click, one menu
        if (!t || Date.now() - t.at > WINDOW_MS) {
            return;
        }
        if (!isItemMenu(sheet)) {
            if (!sheet.querySelector('.actionSheetMenuItem')) {
                warnOnce('an action sheet without .actionSheetMenuItem entries');
            }
            return;   // another picker
        }
        Promise.all([
            ApiClient.getCurrentUser(),
            ApiClient.getItem(ApiClient.getCurrentUserId(), t.id)
        ]).then(function (r) {
            var user = r[0];
            var item = r[1];
            if (user.Policy && user.Policy.IsAdministrator && TYPES.indexOf(item.Type) >= 0 && document.body.contains(sheet)) {
                addEntry(sheet, item);
            }
        }).catch(function () { /* the item is gone */ });
    }

    // Remembers the item whose own menu is being opened: the "..." of the details page, the menu
    // button of a card or list row, or a right click / long press on a card or row.
    function remember(e, contextMenu) {
        if (!e.target.closest) {
            return;
        }
        var id = null;
        var el;
        if (!contextMenu && e.target.closest('.btnMoreCommands')) {
            id = itemIdFromAddress();
        } else if ((el = e.target.closest(contextMenu ? '.card[data-id], .listItem[data-id]' : '[data-action="menu"]'))) {
            var holder = el.closest('[data-id]');
            id = holder ? holder.getAttribute('data-id') : null;
            if (!id) {
                warnOnce('a menu button outside any [data-id] element');
            }
        }
        trigger = id ? { id: id, at: Date.now() } : null;
    }

    document.addEventListener('click', function (e) { remember(e, false); }, true);
    document.addEventListener('contextmenu', function (e) { remember(e, true); }, true);

    new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
            m.addedNodes.forEach(function (node) {
                if (node.nodeType !== 1) {
                    return;
                }
                var sheet = node.classList.contains('actionSheet') ? node : node.querySelector('.actionSheet');
                if (sheet) {
                    onSheet(sheet);
                }
            });
        });
    }).observe(document.body, { childList: true, subtree: true });
})();
