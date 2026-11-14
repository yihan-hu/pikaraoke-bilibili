(function () {
  $(function () {
    const root = window.pikaraokeConfig.basePath || '';
    const message = text => $('#room-control-status').text(text);
    const OFFSET_LIMIT = 120;
    let offsetDragging = false;

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
      const range = document.getElementById('lyrics-offset-range');
      if (range && skip !== 'range') {
        range.value = Math.max(-5, Math.min(5, offset));
        window.mkPaintRange && window.mkPaintRange(range);
      }
      if (skip !== 'input' && !$('#lyrics-offset').is(':focus')) $('#lyrics-offset').val(offset.toFixed(1));
      const label = offset === 0 ? '0.0 秒' : (offset > 0 ? '提前 ' : '延后 ') + Math.abs(offset).toFixed(1) + ' 秒';
      $('#lyrics-offset-label').text(label);
    }

    function state(data) {
      const lyrics = data.lyrics;
      $('#lyrics-visible').prop('checked', lyrics.visible);
      $('#lyrics-mode').val(lyrics.mode).find('option[value=synced]').prop('disabled', !lyrics.lines.length);
      $('#lyrics-position').val(lyrics.position);
      if (!offsetDragging) showOffset(lyrics.offset);
      $('#lyrics-selected').text(lyrics.title ? '已选择：' + lyrics.title : '请为当前歌曲选择歌词');
      $('#danmaku-enabled').prop('checked', data.danmaku_enabled);
      $('#danmaku-send').prop('disabled', !data.danmaku_enabled);
    }

    async function update(data, quiet) {
      try { state(await api('/room/lyrics', data)); if (!quiet) message('歌词设置已更新'); }
      catch(error) { message(error.message); }
    }

    const sendOffset = (() => {
      let timer = null;
      return (value, delay) => {
        clearTimeout(timer);
        timer = setTimeout(() => update({offset: clampOffset(value)}, true), delay);
      };
    })();

    $(document).off('.roomControl');

    // Tabs
    $(document).on('click.roomControl', '.mk-room-tabs [role=tab]', function () {
      const tabs = $(this).closest('.mk-room-tabs').find('[role=tab]');
      tabs.attr('aria-selected', 'false');
      $(this).attr('aria-selected', 'true');
      tabs.each(function () { $('#' + $(this).attr('aria-controls')).prop('hidden', true); });
      $('#' + $(this).attr('aria-controls')).prop('hidden', false);
      try { sessionStorage.setItem('mkRoomTab', this.id); } catch (e) { /* storage unavailable */ }
    });
    try {
      const saved = sessionStorage.getItem('mkRoomTab');
      if (saved && document.getElementById(saved)) $('#' + saved).trigger('click');
    } catch (e) { /* storage unavailable */ }

    $(document).on('click.roomControl', '#lyrics-search', async function () {
      const query = $('#lyrics-query').val().trim();
      if (!query) return message('请输入歌名或歌手');
      $(this).prop('disabled', true); message('正在搜索歌词…');
      try {
        const data = await api('/room/lyrics/search?q=' + encodeURIComponent(query));
        $('#lyrics-results').empty();
        data.results.forEach(item => {
          const button = $('<button type="button" class="button is-light mk-lyrics-result">');
          button.text(item.title + ' — ' + item.artist + (item.synced ? ' · 同步版' : ' · 普通版'));
          button.on('click', () => update({id:item.id}));
          $('#lyrics-results').append(button);
        });
        message(data.results.length ? '选择一项，用于当前歌曲' : '未找到结果，可换个关键词或粘贴歌词');
      } catch(error) { message(error.message); }
      finally { $(this).prop('disabled', false); }
    });
    $(document).on('keydown.roomControl', '#lyrics-query', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); $('#lyrics-search').trigger('click'); }
    });
    $(document).on('change.roomControl', '#lyrics-visible', function () { update({visible:this.checked}); });
    $(document).on('change.roomControl', '#lyrics-mode', function () { update({mode:this.value}); });
    $(document).on('change.roomControl', '#lyrics-position', function () { update({position:this.value}); });

    // Sync offset: live while dragging, final value on release.
    $(document).on('pointerdown.roomControl', '#lyrics-offset-range', () => { offsetDragging = true; });
    $(document).on('input.roomControl', '#lyrics-offset-range', function () {
      offsetDragging = true;
      showOffset(this.value, 'range');
      sendOffset(this.value, 150);
    });
    $(document).on('change.roomControl', '#lyrics-offset-range', function () {
      offsetDragging = false;
      showOffset(this.value, 'range');
      sendOffset(this.value, 0);
    });
    $(document).on('change.roomControl', '#lyrics-offset', function () {
      const value = clampOffset(this.value);
      this.value = value.toFixed(1);
      showOffset(value, 'input');
      sendOffset(value, 0);
    });
    $(document).on('keydown.roomControl', '#lyrics-offset', function (event) {
      if (event.key === 'Enter') { event.preventDefault(); $(this).trigger('change').blur(); }
    });
    $(document).on('click.roomControl', '.lyrics-offset-step', function () {
      const current = clampOffset($('#lyrics-offset').val());
      const value = clampOffset(current + Number($(this).data('step')));
      showOffset(value);
      sendOffset(value, 200);
    });
    $(document).on('click.roomControl', '#lyrics-offset-reset', () => { showOffset(0); sendOffset(0, 0); });

    $(document).on('click.roomControl', '#lyrics-apply-manual', () => update({text:$('#lyrics-manual').val()}));

    async function sendDanmaku() {
      const button = $('#danmaku-send');
      const text = $('#danmaku-text').val().trim();
      if (!text) return message('请输入弹幕');
      button.prop('disabled', true);
      try { await api('/room/danmaku', {text,name:resolveSinger()}); $('#danmaku-text').val(''); message('弹幕已发送 ✨'); }
      catch(error) { message(error.message); }
      finally { button.prop('disabled', false); }
    }
    $(document).on('click.roomControl', '#danmaku-send', sendDanmaku);
    $(document).on('keydown.roomControl', '#danmaku-text', function (event) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendDanmaku(); }
    });

    async function settings(data) {
      try { state(await api('/room/danmaku/settings', data)); message('弹幕设置已更新'); }
      catch(error) { message(error.message); }
    }
    $(document).on('change.roomControl', '#danmaku-enabled', function () { settings({enabled:this.checked}); });
    $(document).on('click.roomControl', '#danmaku-clear', () => settings({clear:true}));

    api('/room/state').then(state).catch(error => message(error.message));
    if (window.socket) { if (window.roomControlHandler) window.socket.off('room_overlays', window.roomControlHandler); window.roomControlHandler = state; window.socket.on('room_overlays', state); }
  });
})();
