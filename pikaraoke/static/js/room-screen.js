(function () {
  $(function () {
    const root = window.pikaraokeConfig.basePath || '';
    const lyricsRoot = document.createElement('section');
    lyricsRoot.id = 'room-lyrics'; lyricsRoot.hidden = true;
    const commentsRoot = document.createElement('section');
    commentsRoot.id = 'room-danmaku';
    document.body.append(lyricsRoot, commentsRoot);
    let state = {lyrics:{visible:false,lines:[],plain:[]},danmaku_enabled:true};
    let layoutKey = '';
    let lastLine = -2;
    let laneReady = [];
    let pending = [];
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');

    function clearComments() {
      commentsRoot.getAnimations({subtree:true}).forEach(animation => animation.cancel());
      commentsRoot.replaceChildren(); pending = []; laneReady = [];
    }

    function fitPlain() {
      const grid = lyricsRoot.querySelector('.lyrics-plain-grid');
      if (!grid || lyricsRoot.hidden) return;
      const lines = state.lyrics.plain;
      const width = grid.clientWidth, height = grid.clientHeight;
      if (!width || !height || !lines.length) return;
      context.font = '600 100px "Microsoft YaHei UI", "PingFang SC", sans-serif';
      const widths = lines.map(line => Math.max(1, context.measureText(line || ' ').width / 100));
      let chosen = {font:2,columns:8};
      outer: for (let font = Math.min(40, Math.floor(innerHeight * .05)); font >= 2; font--) {
        for (let columns = 1; columns <= 8; columns++) {
          const perColumn = Math.ceil(lines.length / columns);
          const columnWidth = (width - (columns - 1) * innerWidth * .02) / columns;
          if (columnWidth <= 0) continue;
          let fits = true;
          for (let column = 0; column < columns; column++) {
            const totalRows = widths.slice(column * perColumn, (column + 1) * perColumn)
              .reduce((sum, value) => sum + Math.max(1, Math.ceil(value * font / columnWidth)), 0);
            if (totalRows * font * 1.4 > height) { fits = false; break; }
          }
          if (fits) { chosen = {font,columns}; break outer; }
        }
      }
      grid.style.gridTemplateColumns = `repeat(${chosen.columns},minmax(0,1fr))`;
      grid.style.fontSize = chosen.font + 'px';
      const perColumn = Math.ceil(lines.length / chosen.columns);
      grid.replaceChildren();
      for (let column = 0; column < chosen.columns; column++) {
        const node = document.createElement('div'); node.className = 'lyrics-plain-column';
        lines.slice(column * perColumn, (column + 1) * perColumn).forEach(line => {
          const item = document.createElement('div'); item.className = 'lyrics-plain-line';
          item.textContent = line || '\u00a0'; node.append(item);
        });
        grid.append(node);
      }
      // Account for actual fallback fonts and line wrapping, retaining every line.
      for (let attempt = 0; attempt < 40; attempt++) {
        const fits = Array.from(grid.children).every(column => column.scrollHeight <= height && column.scrollWidth <= column.clientWidth + 1);
        if (fits) break;
        chosen.font *= .92; grid.style.fontSize = chosen.font + 'px';
      }
    }

    function renderLyrics() {
      const lyrics = state.lyrics;
      const playing = typeof nowPlaying === 'undefined' ? {} : nowPlaying;
      const show = lyrics.visible && lyrics.track_key && lyrics.track_key === playing.media_key;
      lyricsRoot.hidden = !show;
      if (!show) { layoutKey = ''; return; }
      const key = JSON.stringify([lyrics.track_key,lyrics.mode,lyrics.title,lyrics.plain,lyrics.lines,lyrics.position]);
      if (key !== layoutKey) {
        layoutKey = key; lastLine = -2; lyricsRoot.replaceChildren();
        lyricsRoot.className = lyrics.mode === 'plain' ? 'full-sheet' : (lyrics.position === 'top' ? 'at-top' : '');
        if (lyrics.mode === 'plain') {
          const title = document.createElement('div'); title.className = 'lyrics-sheet-title'; title.textContent = lyrics.title;
          const grid = document.createElement('div'); grid.className = 'lyrics-plain-grid';
          lyricsRoot.append(title, grid); requestAnimationFrame(fitPlain);
        } else {
          for (let i = 0; i < 3; i++) {
            const line = document.createElement('div'); line.className = 'lyric-line' + (i === 1 ? ' current' : ''); lyricsRoot.append(line);
          }
        }
      }
      if (lyrics.mode !== 'synced') return;
      const video = document.getElementById('video');
      const position = ((video && Number.isFinite(video.currentTime)) ? video.currentTime : playing.now_playing_position || 0) + Number(lyrics.offset || 0);
      let index = -1;
      for (let i = 0; i < lyrics.lines.length && lyrics.lines[i].time <= position; i++) index = i;
      if (index === lastLine) return;
      lastLine = index;
      lyricsRoot.children[0].textContent = lyrics.lines[index - 1]?.text || '';
      lyricsRoot.children[1].textContent = lyrics.lines[index]?.text || '♪';
      lyricsRoot.children[2].textContent = lyrics.lines[index + 1]?.text || '';
    }

    function pumpComments() {
      if (!state.danmaku_enabled || !pending.length) return;
      const count = Math.max(1, Math.min(5, Math.floor(innerHeight * (state.lyrics.mode === 'plain' && !lyricsRoot.hidden ? .2 : .48) / 110)));
      const now = performance.now();
      let lane = -1;
      for (let i = 0; i < count; i++) if (!laneReady[i] || laneReady[i] <= now) { lane = i; break; }
      if (lane < 0) return;
      const item = pending.shift();
      const node = document.createElement('article'); node.className = 'room-comment'; node.style.top = (20 + lane * 110) + 'px';
      const body = document.createElement('div'); body.className = 'room-comment-body'; body.textContent = item.text;
      const name = document.createElement('div'); name.className = 'room-comment-name'; name.textContent = item.name;
      node.append(body, name); commentsRoot.append(node);
      const width = node.getBoundingClientRect().width;
      const speed = 160;
      laneReady[lane] = now + (width + 90) / speed * 1000;
      const animation = node.animate([{transform:'translateX(0)'},{transform:`translateX(-${innerWidth + width + 120}px)`}], {duration:(innerWidth + width + 120) / speed * 1000,easing:'linear',fill:'forwards'});
      animation.onfinish = () => node.remove();
    }

    function apply(data) {
      state = data;
      if (!state.danmaku_enabled) clearComments();
      renderLyrics();
    }
    const connection = typeof socket === 'undefined' ? window.socket : socket;
    if (connection) {
      connection.on('room_overlays', apply);
      connection.on('danmaku_clear', clearComments);
      connection.on('danmaku', item => { if (state.danmaku_enabled) { if (pending.length < 40) pending.push(item); pumpComments(); } });
      connection.on('connect', () => fetch(root + '/room/state').then(response => response.json()).then(apply).catch(() => {}));
    }
    fetch(root + '/room/state').then(response => response.json()).then(apply).catch(() => {});
    addEventListener('resize', () => requestAnimationFrame(fitPlain));
    setInterval(() => { renderLyrics(); pumpComments(); }, 200);
  });
})();
