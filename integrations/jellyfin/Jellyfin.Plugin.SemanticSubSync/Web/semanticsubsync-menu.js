// SemanticSubSync: adds "Sync subtitles" to the "..." menu of movies and episodes (administrators).
// The menu is Jellyfin's action sheet; the item comes from the card whose menu button was clicked,
// or from the address of a details page.
(function () {
    'use strict';
    if (window.semanticSubSyncMenu) {
        return;
    }
    window.semanticSubSyncMenu = true;

    var TYPES = ['Movie', 'Episode'];
    var LABELS = {
        corrected: 'corrected',
        in_sync: 'already in sync',
        unsure: 'left alone (unsure)',
        no_reference: 'no embedded subtitle to compare with',
        redundant: 'the video already has this subtitle',
        unchanged: 'unchanged since last time',
        skipped: 'skipped',
        read_only: 'cannot write in the folder (read-only)',
        error: 'error (see the Jellyfin log)',
        no_subtitle: 'no external .srt subtitle',
        engine_unavailable: 'the engine could not be installed (see the Jellyfin log)'
    };
    var lastCard = null;

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

    // The sheet is a dialog with its own history entry: going back closes it, as the back button
    // of a remote does. Escape first, for clients that close dialogs on it.
    function close(sheet) {
        document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
        setTimeout(function () {
            if (document.body.contains(sheet)) {
                history.back();
            }
        }, 150);
    }

    function addEntry(sheet, item) {
        var list = sheet.querySelector('.actionSheetScroller') || sheet;
        if (list.querySelector('.sssSyncEntry')) {
            return;
        }
        var button = document.createElement('button');
        button.setAttribute('is', 'emby-button');
        button.type = 'button';
        button.className = 'listItem listItem-button actionSheetMenuItem sssSyncEntry';
        button.innerHTML =
            '<span class="actionsheetMenuItemIcon listItemIcon listItemIcon-transparent material-icons subtitles" aria-hidden="true"></span>' +
            '<div class="listItemBody actionsheetListItemBody"><div class="listItemBodyText actionSheetItemText">Sync subtitles</div></div>';
        button.addEventListener('click', function (e) {
            e.stopPropagation();   // the sheet's own handler would act on an id it does not know
            close(sheet);
            sync(item);
        });
        list.appendChild(button);
    }

    function onSheet(sheet) {
        var id = lastCard && Date.now() - lastCard.at < 1500 ? lastCard.id : itemIdFromAddress();
        if (!id) {
            return;
        }
        Promise.all([
            ApiClient.getCurrentUser(),
            ApiClient.getItem(ApiClient.getCurrentUserId(), id)
        ]).then(function (r) {
            var user = r[0];
            var item = r[1];
            if (user.Policy && user.Policy.IsAdministrator && TYPES.indexOf(item.Type) >= 0 && document.body.contains(sheet)) {
                addEntry(sheet, item);
            }
        }).catch(function () { /* not an item menu */ });
    }

    document.addEventListener('click', function (e) {
        var more = e.target.closest ? e.target.closest('[data-action="menu"]') : null;
        var card = more ? more.closest('[data-id]') : null;
        lastCard = card ? { id: card.getAttribute('data-id'), at: Date.now() } : lastCard;
    }, true);

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
