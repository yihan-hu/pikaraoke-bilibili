(function () {
  let mountedRoom;
  let unbindSocket;

  window.mountRoomControls = function () {
    const panel = document.getElementById('room-controls');
    if (!panel || mountedRoom === panel) return;
    if (unbindSocket) unbindSocket();
    mountedRoom = panel;
    const $panel = $(panel);
    const find = selector => $panel.find(selector);
    const root = window.pikaraokeConfig.basePath || '';
    const message = text => find('#room-control-status').text(text);
    const candidateSelect = find('#lyrics-results');
    const OFFSET_LIMIT = 120;
    let offsetDragging = false;
    let trackKey = null;
    let lookup = {results: []};
    let selectedId = null;
    let hasSyncedLyrics = false;
    let manualResults = null;
    let searchRequest = 0;
    let candidateRequest = null;
    let socketVersion = 0;
    let danmakuEnabled = true;
    let sendingDanmaku = false;

    async function api(path, data) {
      const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Pikaraoke-Settings':'1'},body:JSON.stringify(data)};
      const response = await fetch(root + path, options);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '请求失败');
      return result;
    }

    function clampOffset(value) {
      const number = Math.round(Number(value) * 10) / 10;
      if (!Number.isFinite(number)) return 0;
      return Math.max(-OFFSET_LIMIT, Math.min(OFFSET_LIMIT, number));
    }

    // Slider, number box and label always show the same value; the slider just
    // pins to its ±5 s ends when a typed value goes further.
    function showOffset(value, skip) {
      const offset = clampOffset(value);
      const range = find('#lyrics-offset-range')[0];
      if (range && skip !== 'range') {
        range.value = Math.max(-5, Math.min(5, offset));
        window.mkPaintRange && window.mkPaintRange(range);
      }
      const input = find('#lyrics-offset');
      if (skip !== 'input' && !input.is(':focus')) input.val(offset.toFixed(1));
      const label = offset === 0 ? '0.0 秒' : (offset > 0 ? '提前 ' : '延后 ') + Math.abs(offset).toFixed(1) + ' 秒';
      find('#lyrics-offset-label').text(label);
    }

    function renderCandidates() {
      const results = (manualResults ?? lookup.results).filter(item => item.id !== selectedId);
      const prompt = results.length ? '选择其他歌词版本' : '暂无其他歌词版本';
      const options = [new Option(prompt, '')];
      for (const item of results) {
        const version = item.synced ? '同步版' : '普通版';
        options.push(new Option(`${item.title} — ${item.artist} · ${version}`, item.id));
      }
      candidateSelect.empty().append(options)
        .prop('disabled', !trackKey || !results.length || candidateRequest !== null);
    }

    function renderGoogleFallback() {
      const query = (find('#lyrics-query').val() || lookup.query || '').trim();
      const show = !!trackKey && !!query && !hasSyncedLyrics && lookup.status !== 'searching';
      const fallback = find('#lyrics-google').prop('hidden', !show);
      if (show) {
        const keyword = /[\u3400-\u9fff]/.test(query) ? '歌词' : 'lyrics';
        const params = new URLSearchParams({q: query + ' ' + keyword});
        fallback.find('a').attr('href', 'https://www.google.com/search?' + params);
      }
    }

    function state(data) {
      if (!panel.isConnected) return;
      const previousLookup = lookup;
      lookup = data.lyrics_lookup;
      const changedTrack = lookup.track_key !== trackKey;
      if (changedTrack) {
        trackKey = lookup.track_key;
        manualResults = null;
        searchRequest++;
        candidateRequest = null;
        offsetDragging = false;
        find('#lyrics-query').val(lookup.query);
        find('#lyrics-search').prop('disabled', false);
        find('#lyrics-manual').val('');
        find('#lyrics-offset').val('0.0');
        message('');
      }

      const lyrics = data.lyrics;
      const hasSelection = !!trackKey && lyrics.track_key === trackKey;
      hasSyncedLyrics = hasSelection && lyrics.lines.length > 0;
      const oldSelectedId = selectedId;
      selectedId = hasSelection ? lyrics.id : null;
      find('#lyrics-auto-search').prop('checked', data.lyrics_auto_search);
      find('#lyrics-visible').prop({checked: hasSelection && lyrics.visible, disabled: !hasSelection});
      find('#lyrics-mode').val(hasSelection ? lyrics.mode : 'plain').prop('disabled', !hasSelection)
        .find('option[value=synced]').prop('disabled', !hasSelection || !lyrics.lines.length);
      find('#lyrics-position').val(lyrics.position);
      find('#lyrics-apply-manual').prop('disabled', !trackKey);
      find('#lyrics-offset-range, #lyrics-offset, .lyrics-offset-step, #lyrics-offset-reset')
        .prop('disabled', !hasSelection);
      if (!offsetDragging) showOffset(hasSelection ? lyrics.offset : 0);
      const status = {
        idle: '请为当前歌曲选择歌词',
        searching: '正在自动搜索歌词…',
        ready: '未找到匹配歌词，可手动搜索或粘贴歌词',
        error: '歌词服务暂时不可用，可手动搜索或粘贴歌词'
      };
      find('#lyrics-selected').text(
        hasSelection ? '已选择：' + lyrics.title : (trackKey ? status[lookup.status] : '暂无播放中的歌曲')
      );
      renderGoogleFallback();
      if (changedTrack || oldSelectedId !== selectedId || previousLookup.status !== lookup.status) {
        renderCandidates();
      }
      danmakuEnabled = data.danmaku_enabled;
      find('#danmaku-enabled').prop('checked', danmakuEnabled);
      find('#danmaku-send').prop('disabled', !danmakuEnabled || sendingDanmaku);
    }

    async function requestState(path, data) {
      const version = socketVersion;
      const result = await api(path, data);
      if (version === socketVersion) state(result);
    }

    async function update(data, quiet) {
      if (!panel.isConnected) return;
      try {
        await requestState('/room/lyrics', data);
        if (!quiet && (!('track_key' in data) || data.track_key === trackKey)) message('歌词设置已更新');
      } catch(error) { message(error.message); }
    }

    const sendOffset = (() => {
      let timer = null;
      return (value, delay) => {
        clearTimeout(timer);
        const key = trackKey;
        if (key) timer = setTimeout(() => {
          if (key === trackKey) update({offset: clampOffset(value), track_key: key}, true);
        }, delay);
      };
    })();

    // Tabs
    $panel.on('click', '.mk-room-tabs [role=tab]', function () {
      const tabs = $(this).closest('.mk-room-tabs').find('[role=tab]');
      tabs.attr('aria-selected', 'false');
      $(this).attr('aria-selected', 'true');
      tabs.each(function () { find('#' + this.getAttribute('aria-controls')).prop('hidden', true); });
      find('#' + this.getAttribute('aria-controls')).prop('hidden', false);
      try { sessionStorage.setItem('mkRoomTab', this.id); } catch (e) { /* storage unavailable */ }
    });
    try {
      const saved = sessionStorage.getItem('mkRoomTab');
      if (saved) find('[role=tab]').filter(function () { return this.id === saved; }).trigger('click');
    } catch (e) { /* storage unavailable */ }

    $panel.on('click', '#lyrics-search', async function () {
      const query = find('#lyrics-query').val().trim();
      if (!query) return message('请输入歌名或歌手');
      const request = ++searchRequest;
      $(this).prop('disabled', true); message('正在搜索歌词…');
      try {
        const data = await api('/room/lyrics/search?q=' + encodeURIComponent(query));
        if (!panel.isConnected || request !== searchRequest) return;
        manualResults = data.results;
        renderCandidates();
        message(manualResults.length ? '选择一个歌词版本' : '未找到结果，可换个关键词或粘贴歌词');
      } catch(error) {
        if (request === searchRequest) message(error.message);
      } finally {
        if (request === searchRequest) $(this).prop('disabled', false);
      }
    });
    $panel.on('keydown', '#lyrics-query', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); find('#lyrics-search').trigger('click'); }
    });
    $panel.on('input', '#lyrics-query', renderGoogleFallback);
    $panel.on('click', '#lyrics-google a', () => find('#lyrics-manual-panel').prop('open', true));
    $panel.on('change', '#lyrics-results', async function () {
      if (!this.value || !trackKey || candidateRequest) return;
      const request = update({id: Number(this.value), track_key: trackKey});
      candidateRequest = request;
      candidateSelect.prop('disabled', true);
      await request;
      if (candidateRequest === request) {
        candidateRequest = null;
        renderCandidates();
      }
    });
    $panel.on('change', '#lyrics-auto-search', function () { update({auto_search:this.checked}); });
    $panel.on('change', '#lyrics-visible', function () { update({visible:this.checked, track_key:trackKey}); });
    $panel.on('change', '#lyrics-mode', function () { update({mode:this.value, track_key:trackKey}); });
    $panel.on('change', '#lyrics-position', function () { update({position:this.value}); });

    // Sync offset: live while dragging, final value on release.
    $panel.on('input', '#lyrics-offset-range', function () {
      offsetDragging = true;
      showOffset(this.value, 'range');
      sendOffset(this.value, 150);
    });
    $panel.on('change', '#lyrics-offset-range', function () {
      offsetDragging = false;
      showOffset(this.value, 'range');
      sendOffset(this.value, 0);
    });
    $panel.on('change', '#lyrics-offset', function () {
      const value = clampOffset(this.value);
      this.value = value.toFixed(1);
      showOffset(value, 'input');
      sendOffset(value, 0);
    });
    $panel.on('keydown', '#lyrics-offset', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); $(this).trigger('change').blur(); }
    });
    $panel.on('click', '.lyrics-offset-step', function () {
      const current = clampOffset(find('#lyrics-offset').val());
      const value = clampOffset(current + Number($(this).data('step')));
      showOffset(value);
      sendOffset(value, 200);
    });
    $panel.on('click', '#lyrics-offset-reset', () => { showOffset(0); sendOffset(0, 0); });
    $panel.on('click', '#lyrics-apply-manual', () => {
      if (trackKey) update({text: find('#lyrics-manual').val(), track_key: trackKey});
    });

    async function sendDanmaku() {
      if (!danmakuEnabled || sendingDanmaku) return;
      const button = find('#danmaku-send');
      const input = find('#danmaku-text');
      const text = input.val().trim();
      if (!text) return message('请输入弹幕');
      sendingDanmaku = true;
      button.prop('disabled', true);
      try {
        await api('/room/danmaku', {text, name:resolveSinger()});
        input.val('');
        message('弹幕已发送 ✨');
      } catch(error) { message(error.message); }
      finally {
        sendingDanmaku = false;
        button.prop('disabled', !danmakuEnabled);
      }
    }
    $panel.on('click', '#danmaku-send', sendDanmaku);
    $panel.on('keydown', '#danmaku-text', function (event) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendDanmaku(); }
    });

    async function settings(data) {
      try {
        await requestState('/room/danmaku/settings', data);
        message('弹幕设置已更新');
      } catch(error) { message(error.message); }
    }
    $panel.on('change', '#danmaku-enabled', function () { settings({enabled:this.checked}); });
    $panel.on('click', '#danmaku-clear', () => settings({clear:true}));

    function refresh() {
      if (!panel.isConnected) return;
      requestState('/room/state').catch(error => message(error.message));
    }

    const socket = window.socket;
    if (socket) {
      const receive = data => { socketVersion++; state(data); };
      socket.on('room_overlays', receive);
      socket.on('connect', refresh);
      unbindSocket = () => {
        socket.off('room_overlays', receive);
        socket.off('connect', refresh);
      };
    }
    refresh();
  };
})();
