const withBasePath = (path) => `${window.pikaraokeConfig.basePath}${path}`;
let socket = io({ path: window.pikaraokeConfig.socketioPath });
let mouseTimer = null;
let cursorVisible = false;
let nowPlaying = {};
let octopusInstance = null;
let showMenu = false;
let menuButtonVisible = false;
let autoplayConfirmed = false;
let volume = 0.85;
const playbackStartTimeout = 10000;
const bgMediaResumeDelay = 2000;
let isScoreShown = false;
const hasBgVideo = PikaraokeConfig.hasBgVideo;
let currentVideoUrl = null;
let hlsInstance = null;
let idleTime = 0;
let screensaverTimeoutSeconds = PikaraokeConfig.screensaverTimeout;
let bg_playlist = [];
let bgMediaResumeTimeout = null;
let scoreReviews = {
  low: ["Better luck next time!"],
  mid: ["Not bad!"],
  high: ["Great job!"],
};
let isMaster = false;
let uiScale = null;
let clockIntervalId = null;
let mediaGeneration = 0;
let retryTimer = null;
let startTimer = null;
let lastPosition = 0;
let endingStream = null;
let backgroundGeneration = 0;

// Browser detection
const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const isMobileSafari = isSafari && (/iPhone|iPad|iPod/i.test(navigator.userAgent) || navigator.maxTouchPoints > 1);
const isChrome = /chrome/i.test(navigator.userAgent) && !/edg/i.test(navigator.userAgent);
const isFirefox = /firefox/i.test(navigator.userAgent);
const isEdge = /edg/i.test(navigator.userAgent);
const isSupportedBrowser = isSafari || isChrome || isFirefox || isEdge;

const isMediaPlaying = (media) =>
  !!(
    media.currentTime > 0 &&
    !media.paused &&
    !media.ended &&
    media.readyState > 2
  );

const formatTime = (seconds) => {
  if (isNaN(seconds)) {
    return "00:00";
  }
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  const formattedMinutes = String(minutes).padStart(2, "0");
  const formattedSeconds = String(secs).padStart(2, "0");
  return `${formattedMinutes}:${formattedSeconds}`;
}

const testAutoplayCapability = async () => {
  // Test if autoplay with audio is allowed using a real video file
  try {
    const testVideo = document.createElement('video');
    testVideo.playsInline = true;
    testVideo.muted = true;  // Start muted (always allowed)
    testVideo.src = withBasePath("/static/video/test_autoplay.mp4");

    // Wait for video to be ready
    await new Promise((resolve, reject) => {
      testVideo.onloadeddata = resolve;
      testVideo.onerror = reject;
    });

    await testVideo.play();
    // Now try to unmute - this is the real test
    testVideo.muted = false;
    testVideo.volume = 0.01;

    // Brief delay to let browser enforce policy
    await new Promise(resolve => setTimeout(resolve, 500));

    // Check if browser paused or muted the video
    if (testVideo.muted || testVideo.paused) {
      testVideo.pause();
      $('#permissions-modal').addClass('is-active');
    } else {
      testVideo.pause();
      handleConfirmation();
    }
  } catch (e) {
    // Autoplay blocked
    console.log("Autoplay error thrown", e);
    $('#permissions-modal').addClass('is-active');
  }
};

const handleConfirmation = () => {
  $('#permissions-modal').removeClass('is-active');
  autoplayConfirmed = true;
  updateBackgroundMediaState(true);
  loadNowPlaying();
};

const hideVideo = () => {
  $("#video-container").hide();
}

const stopMainMedia = () => {
  mediaGeneration++;
  clearTimeout(retryTimer); retryTimer = null;
  clearTimeout(startTimer); startTimer = null;
  if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
  const video = getVideoPlayer();
  if (!video) return;
  $(video).stop(true, false);
  video.pause(); video.removeAttribute('src');
  $('#video-source').removeAttr('src'); video.load();
  currentVideoUrl = null;
  hideVideo();
};

const isOnlineTrack = () => Boolean(currentVideoUrl && currentVideoUrl.includes('/bilibili-preview?'));

const reportBuffering = (buffering) => {
  if (isMaster && currentVideoUrl) socket.emit('playback_buffering', {
    stream_url:currentVideoUrl, buffering, duration:getVideoPlayer().duration
  });
};

const scheduleOnlineRecovery = () => {
  if (!isOnlineTrack() || retryTimer || nowPlaying.is_paused) return;
  reportBuffering(true);
  const track = currentVideoUrl;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    if (track !== currentVideoUrl || nowPlaying.is_paused) return;
    const video = getVideoPlayer();
    if (Number.isFinite(video.currentTime) && video.currentTime > 0) lastPosition = video.currentTime;
    loadTrack(track, lastPosition, true);
  }, 10000);
};

const endSong = async (reason = null, showScore = false) => {
  const track = currentVideoUrl;
  if (!track || endingStream === track) return;
  if (reason !== 'complete') { reportBuffering(true); if (isOnlineTrack()) scheduleOnlineRecovery(); return; }
  const video = getVideoPlayer();
  const expected = Number(nowPlaying.now_playing_duration || video.duration);
  if (isOnlineTrack() && Number.isFinite(expected) && video.currentTime < expected - Math.max(5, expected * .03)) {
    scheduleOnlineRecovery(); return;
  }
  endingStream = track;
  const score = showScore && !PikaraokeConfig.disableScore;
  isScoreShown = score;
  stopMainMedia();
  const generation = mediaGeneration;
  if (score) await startScore(withBasePath('/static/'));
  if (generation !== mediaGeneration || nowPlaying.now_playing_url !== track) return;
  isScoreShown = false;
  if (isMaster) socket.emit('end_song', {reason, stream_url:track});
  endingStream = null;
};

const loadTrack = (track, position = 0, recovering = false) => {
  stopMainMedia();
  currentVideoUrl = track; endingStream = null; lastPosition = position;
  const generation = mediaGeneration;
  const video = getVideoPlayer();
  video.muted = !isMaster;
  let mediaUrl = track;
  if (recovering && isOnlineTrack()) mediaUrl += '&retry=' + Date.now();
  video.addEventListener('loadedmetadata', () => {
    if (generation !== mediaGeneration) return;
    if (position > 0) video.currentTime = Math.min(position, Number.isFinite(video.duration) ? Math.max(0, video.duration - .2) : position);
    if (!nowPlaying.is_paused) video.play().catch(() => {});
  }, {once:true});
  if (track.endsWith('.m3u8')) {
    const native = video.canPlayType('application/vnd.apple.mpegurl') && !isChrome && !isEdge && !isMobileSafari;
    if (native) { video.src = mediaUrl; video.load(); }
    else { hlsInstance = new Hls({startPosition:position}); hlsInstance.loadSource(mediaUrl); hlsInstance.attachMedia(video); }
  } else { video.src = mediaUrl; video.load(); }
  $('#video-container').show();
  playBGMusic(false); playBGVideo(false);
  if (!nowPlaying.is_paused) video.play().catch(() => {});
  startTimer = setTimeout(() => {
    if (generation !== mediaGeneration || nowPlaying.is_paused || isMediaPlaying(video)) return;
    if (isOnlineTrack()) scheduleOnlineRecovery();
    else endSong('failed to start');
  }, playbackStartTimeout);
};

const getBackgroundMusicPlayer = () => document.getElementById('background-music');
const getBackgroundVideoPlayer = () => document.getElementById('bg-video');
const getVideoPlayer = () => $("#video")[0]

const getNextBgMusicSong = () => {
  let currentSong = getBackgroundMusicPlayer().getAttribute('src');
  let nextSong = bg_playlist[0];
  if (currentSong) {
    let currentIndex = bg_playlist.indexOf(currentSong);
    if (currentIndex >= 0 && currentIndex < bg_playlist.length - 1) {
      nextSong = bg_playlist[currentIndex + 1];
    }
  }
  return nextSong;
}

const playBGMusic = async (play) => {
  const audio = getBackgroundMusicPlayer();
  if (!audio) return;
  const generation = ++backgroundGeneration;
  $(audio).stop(true, false);
  if (!play || !shouldBackgroundMediaPlay() || PikaraokeConfig.disableBgMusic) {
    audio.pause(); audio.volume = 0; return;
  }
  if (!bg_playlist.length || isMediaPlaying(audio)) return;
  if (!audio.getAttribute('src')) audio.setAttribute('src', getNextBgMusicSong());
  audio.volume = 0;
  try { await audio.play(); } catch(e) { return; }
  if (generation !== backgroundGeneration || !shouldBackgroundMediaPlay()) { audio.pause(); return; }
  $(audio).animate({volume:PikaraokeConfig.bgMusicVolume}, 1000);
};

const playBGVideo = async (play) => {
  const bgVideo = getBackgroundVideoPlayer();
  const bgVideoContainer = $('#bg-video-container');
  if (!bgVideo) return;

  if (play) {
    if (PikaraokeConfig.disableBgVideo || !shouldBackgroundMediaPlay()) return;
    if (!autoplayConfirmed) return;

    if (isMediaPlaying(bgVideo)) return;
    $("#bg-video").attr("src", withBasePath("/stream/bg_video"));
    if (bgVideo.readyState <= 2) await bgVideo.load();
    bgVideo.play().catch(() => console.log("Autoplay blocked (video)"));
    bgVideoContainer.fadeIn(2000);
  } else {
    if (bgVideo) {
      bgVideo.pause();
      bgVideoContainer.fadeOut(2000);
    }
  }
}

const shouldBackgroundMediaPlay = () => {
  return autoplayConfirmed && isMaster && !isScoreShown && !currentVideoUrl &&
    !nowPlaying.now_playing &&
    !nowPlaying.up_next;
};

const updateBackgroundMediaState = (immediate = false) => {
  // Clear any pending resume
  if (bgMediaResumeTimeout) {
    clearTimeout(bgMediaResumeTimeout);
    bgMediaResumeTimeout = null;
  }

  if (shouldBackgroundMediaPlay()) {
    if (immediate) {
      playBGMusic(true);
      if (hasBgVideo) playBGVideo(true);
    } else {
      bgMediaResumeTimeout = setTimeout(() => {
        bgMediaResumeTimeout = null;
        if (shouldBackgroundMediaPlay()) {
          playBGMusic(true);
          if (hasBgVideo) playBGVideo(true);
        }
      }, bgMediaResumeDelay);
    }
  } else {
    playBGMusic(false);
    playBGVideo(false);
  }
};

const flashNotification = (message, categoryClass) => {
  const sn = $("#splash-notification");
  if (sn.html()) return;
  sn.html(message);
  sn.addClass(categoryClass);
  sn.fadeIn();
  setTimeout(() => {
    sn.fadeOut();
    setTimeout(() => {
      sn.html("");
      sn.removeClass(categoryClass);
    }, 450);
  }, 3000);
}

const setupScreensaver = () => {
  if (screensaverTimeoutSeconds > 0) {
    setInterval(() => {
      let screensaver = document.getElementById('screensaver');
      let video = getVideoPlayer();
      if (isMediaPlaying(video) || cursorVisible) {
        idleTime = 0;
      }
      if (idleTime >= screensaverTimeoutSeconds) {
        if (screensaver.style.visibility === 'hidden') {
          screensaver.style.visibility = 'visible';
          playBGVideo(false);
          startScreensaver(); // depends on upstream screensaver.js import
        }
        if (idleTime > screensaverTimeoutSeconds + 36000) idleTime = screensaverTimeoutSeconds;
      } else {
        if (screensaver.style.visibility === 'visible') {
          screensaver.style.visibility = 'hidden';
          stopScreensaver(); // depends on upstream screensaver.js import
          updateBackgroundMediaState(true);
        }
      }
      idleTime++;
    }, 1000)
  }
}

// Server-rendered once at page load, then kept current from now_playing updates
// because the splash screen is a long-lived display that never reloads.
let sessionName = null;

const renderSessionName = () => {
  $("#session-name")
    .text(sessionName || "")
    .toggle(Boolean(sessionName) && !PikaraokeConfig.hideSessionName);
};

const handleNowPlayingUpdate = (np) => {
  nowPlaying = np;
  if (np.session_name !== sessionName) {
    sessionName = np.session_name;
    renderSessionName();
  }
  if (np.now_playing) {

    // Handle updating now playing HTML
    let nowPlayingHtml = `<span>${$("<div>").text(np.now_playing).html()}</span> `;
    if (np.now_playing_transpose !== 0) {
      nowPlayingHtml += `<span class='is-size-6 has-text-success'><b>Key</b>: ${getSemitonesLabel(np.now_playing_transpose)} </span>`;
    }
    $("#now-playing-song").html(nowPlayingHtml);
    $("#now-playing-singer").text(np.now_playing_user);
    $("#now-playing").fadeIn();
  } else {
    $("#now-playing").fadeOut();
  }
  if (np.up_next) {
    $("#up-next-song").html(np.up_next);
    $("#up-next-singer").html(np.next_user);
    $("#up-next").fadeIn();
  } else {
    $("#up-next").fadeOut();
  }

  // Update bg music and video state
  if (np.now_playing || np.up_next) {
    idleTime = 0;
  }
  updateBackgroundMediaState();

  const video = getVideoPlayer();

  // Setup ASS subtitle file if found
  const subtitleUrl = np.now_playing_subtitle_url;
  if (octopusInstance) {
    octopusInstance.dispose();
    octopusInstance = null;
  }
  if (subtitleUrl && video) {
    const options = {
      video: video,
      subUrl: subtitleUrl,
      fonts: [
        withBasePath("/static/fonts/Arial.ttf"),
        withBasePath("/static/fonts/DroidSansFallback.ttf"),
      ],
      debug: true,
      workerUrl: withBasePath("/static/js/subtitles-octopus-worker.js")
    };
    try {
      octopusInstance = new SubtitlesOctopus(options);
      if (uiScale) {
        // Find the canvas created by SubtitlesOctopus (sibling of the video)
        const canvas = video.parentNode.querySelector('canvas');
        if (canvas) {
          canvas.style.transform = `scale(${uiScale})`;
          canvas.style.transformOrigin = 'bottom center';
        }
      }
    } catch (e) { console.error(e); }
  }

  if (!np.now_playing_url) {
    if (currentVideoUrl) stopMainMedia();
    updateBackgroundMediaState();
    return;
  }
  if (np.now_playing_url !== currentVideoUrl) {
    isScoreShown = false;
    const position = Number(np.resume_position != null ? np.resume_position : (np.now_playing_position || 0));
    loadTrack(np.now_playing_url, position);
  }
  volume = np.volume;
  video.volume = volume;
  video.muted = !isMaster;
  if (np.now_playing_duration) { $('#duration').text('/' + formatTime(np.now_playing_duration)).show(); }
  else $('#duration').hide();

}

async function loadNowPlaying() {
  const data = await $.get(withBasePath("/now_playing"));
  handleNowPlayingUpdate(JSON.parse(data));
}

const setupOverlayMenus = () => {
  if (PikaraokeConfig.hideOverlay) {
    $('#bottom-container').hide();
    $('#top-container').hide();
  }
  $("#menu a").fadeOut(); // start hidden
  const triggerInactivity = () => {
    mouseTimer = null;
    document.body.style.cursor = 'none';
    cursorVisible = false;
    $("#menu a").fadeOut();
    if (PikaraokeConfig.showSplashClock) {
      setTimeout(() => {
        if (!cursorVisible) $("#clock").fadeIn();
      }, 1000);
    }
    menuButtonVisible = false;
  };

  document.onmousemove = function () {
    if (mouseTimer) window.clearTimeout(mouseTimer);
    if (!cursorVisible) {
      document.body.style.cursor = 'default';
      cursorVisible = true;
    }
    if (!menuButtonVisible) {
      $("#menu a").fadeIn();
      $("#clock").hide();
      menuButtonVisible = true;
    }
    mouseTimer = window.setTimeout(triggerInactivity, 5000);
  };

  // Set initial state to hidden
  triggerInactivity();
  $('#menu a').click(function () {
    if (showMenu) {
      $('#menu-container').hide();
      $('#menu-container iframe').attr('src', '');
      showMenu = false;
    } else {
      setUserCookie();
      $("#menu-container").show();
      $("#menu-container iframe").attr("src", withBasePath("/"));
      showMenu = true;
    }
  });
  $('#menu-background').click(function () {
    if (showMenu) {
      $(".navbar-burger").click();
    }
  });
}

const setupVideoPlayer = () => {
  $('#video-container').hide();
  const video = getVideoPlayer();
  video.muted = !isMaster;
  video.addEventListener('playing', () => {
    if (!currentVideoUrl || video.readyState < 2) return;
    clearTimeout(retryTimer); retryTimer = null;
    clearTimeout(startTimer); startTimer = null;
    playBGMusic(false); playBGVideo(false);
    $('#video-container').show();
    if (isMaster) socket.emit('start_song', {stream_url:currentVideoUrl});
    reportBuffering(false);
  });
  for (const event of ['waiting','stalled']) video.addEventListener(event, () => {
    if (!currentVideoUrl) return;
    reportBuffering(true);
    if (isOnlineTrack()) scheduleOnlineRecovery();
  });
  video.addEventListener('error', () => {
    if (!currentVideoUrl) return;
    if (isOnlineTrack()) scheduleOnlineRecovery();
    else endSong('error while playing');
  });
  setInterval(() => {
    if (isMaster && currentVideoUrl && Number.isFinite(video.currentTime)) {
      socket.emit('playback_position', {stream_url:currentVideoUrl, position:video.currentTime});
    }
  }, 1000);
  video.addEventListener('ended', () => endSong('complete', true));
  video.addEventListener('timeupdate', () => {
    if (Number.isFinite(video.currentTime) && video.currentTime > 0) lastPosition = video.currentTime;
    $('#current').text(formatTime(video.currentTime));
  });

}

const setupBackgroundMusicPlayer = () => {
  $.get(withBasePath("/bg_playlist"), function (data) {
    if (data) bg_playlist = data;
  });
  const bgMusic = getBackgroundMusicPlayer();
  bgMusic.addEventListener("ended", async () => {
    if (!shouldBackgroundMediaPlay() || PikaraokeConfig.disableBgMusic) return;
    bgMusic.setAttribute('src', getNextBgMusicSong());
    await playBGMusic(true);
  });
}

const handleUnsupportedBrowser = () => {
  if (!isSupportedBrowser) {
    let modalContents = document.getElementById("permissions-modal-content");
    let warningMessage = document.createElement("p");
    warningMessage.classList.add("notification", "is-warning");
    warningMessage.innerHTML =
      PikaraokeConfig.translations.unsupportedBrowser;
    modalContents.prepend(warningMessage);
  }
}

const startClock = () => {
  if (clockIntervalId) return;
  const update = () => {
    const el = document.getElementById('clock');
    if (el) el.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
  };
  update();
  clockIntervalId = setInterval(update, 1000);
}

const stopClock = () => {
  if (!clockIntervalId) return;
  clearInterval(clockIntervalId);
  clockIntervalId = null;
}

const toggleBGMedia = (configKey, playFn, disabled) => {
  PikaraokeConfig[configKey] = disabled;
  disabled ? playFn(false) : shouldBackgroundMediaPlay() && playFn(true);
};

const PREFERENCE_EFFECTS = {
  disable_bg_video:    (v) => toggleBGMedia("disableBgVideo", playBGVideo, v),
  disable_bg_music:    (v) => toggleBGMedia("disableBgMusic", playBGMusic, v),
  disable_score:       (v) => { PikaraokeConfig.disableScore = v; },
  show_splash_clock:   (v) => {
    PikaraokeConfig.showSplashClock = v;
    v ? startClock() : (stopClock(), $("#clock").hide());
  },
  hide_overlay:        (v) => {
    PikaraokeConfig.hideOverlay = v;
    $("#bottom-container, #top-container").toggle(!v);
  },
  hide_url:            (v) => { $("#qr-code, #screensaver-qr").toggle(!v); },
  hide_logo:           (v) => { $("#logo-container img.logo").toggle(!v); },
  hide_session_name:   (v) => {
    PikaraokeConfig.hideSessionName = v;
    renderSessionName();
  },
  bg_music_volume:     (v) => {
    PikaraokeConfig.bgMusicVolume = v;
    const player = getBackgroundMusicPlayer();
    if (isMediaPlaying(player)) $(player).animate({ volume: v }, 1000);
  },
  screensaver_timeout: (v) => {
    screensaverTimeoutSeconds = v;
    PikaraokeConfig.screensaverTimeout = v;
  },
};

const parsePreferenceValue = (value) => {
  if (typeof value !== "string") return value;
  if (value === "True") return true;
  if (value === "False") return false;
  const num = Number(value);
  return !isNaN(num) && value.trim() !== "" ? num : value;
};

const applyPreferenceUpdate = (data) => {
  const effect = PREFERENCE_EFFECTS[data.key];
  if (effect) effect(parsePreferenceValue(data.value));
};

const applyPreferencesReset = (defaults) => {
  Object.entries(defaults).forEach(([key, value]) => applyPreferenceUpdate({ key, value }));
};

const setupSocketEvents = () => {
  socket.on('connect', () => {
    console.log('Socket connected');
    socket.emit("register_splash", {protocol:2});
  });
  socket.on('splash_role', (role) => {
    isMaster = (role === "master");
    if (getVideoPlayer()) getVideoPlayer().muted = !isMaster;
    if (getBackgroundMusicPlayer()) getBackgroundMusicPlayer().muted = !isMaster;
    updateBackgroundMediaState(true);
    console.log("Splash role assigned:", role, isMaster ? "(Master active)" : "(Slave active - read-only)");
  });
  socket.on('connect_error', (error) => {
    console.error('Connection error:', error);
    flashNotification(PikaraokeConfig.translations.socketConnectionLost, "is-danger");
  });
  socket.on('disconnect', (reason) => {
    isMaster = false;
    if (getVideoPlayer()) getVideoPlayer().muted = true;
    playBGMusic(false);
    console.warn('Socket disconnected:', reason);
    flashNotification(PikaraokeConfig.translations.socketConnectionLost, "is-danger");
  });
  socket.on('pause', () => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (!video.paused) {
      $(video).animate({ volume: 0 }, 1000, () => {
        video.pause();
        video.volume = currVolume;
      });
    }
  });
  socket.on('play', () => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (video.paused) {
      video.play();
      video.volume = 0;
      $(video).animate({ volume: currVolume }, 1000);
    }
  });
  socket.on('skip', () => { stopMainMedia(); });
  socket.on('volume', (val) => {
    const video = getVideoPlayer();
    if (val === "up") {
      video.volume = Math.min(1, video.volume + 0.1);
    } else if (val === "down") {
      video.volume = Math.max(0, video.volume - 0.1);
    } else {
      video.volume = val;
    }
  });
  socket.on('restart', () => {
    const video = getVideoPlayer();
    video.currentTime = 0;
    if (video.paused) video.play();
  });
  socket.on('seek', (position) => {
    const video = getVideoPlayer();
    const target = Number(position);
    if (!currentVideoUrl || !Number.isFinite(target) || target < 0) return;
    const limit = Number.isFinite(video.duration) ? Math.max(0, video.duration - 0.2) : target;
    video.currentTime = Math.min(target, limit);
    lastPosition = video.currentTime;
    if (isMaster) socket.emit('playback_position', {stream_url:currentVideoUrl, position:video.currentTime});
  });
  socket.on("notification", (data) => {
    const notification = data.split("::");
    const message = notification[0];
    const categoryClass = notification.length > 1 ? notification[1] : "is-primary";
    flashNotification(message, categoryClass);
    if (isMaster) {
      socket.emit("clear_notification");
    }
  });
  socket.on("now_playing", handleNowPlayingUpdate);
  socket.on("preferences_update", applyPreferenceUpdate);
  socket.on("preferences_reset", applyPreferencesReset);
  socket.on("score_phrases_update", (phrases) => { scoreReviews = phrases; });

  socket.on("playback_position", (position) => {
    if (!isMaster) {
      const video = getVideoPlayer();
      if (isMediaPlaying(video)) {
        if (Math.abs(video.currentTime - position) > 2) {
          console.log("Slave drifting, syncing position to:", position);
          video.currentTime = position;
        }
      }
    }
  });
}

const handleSocketRecovery = () => {
  // A socket may disconnect if the tab is backgrounded for a while
  // Reconnect and configure event listeners when tab becomes visible again
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === 'visible') {
      autoplayConfirmed && loadNowPlaying();
      if (!socket.connected) {
        socket = io({ path: window.pikaraokeConfig.socketioPath });
        setupSocketEvents();
      }
    }
  });
}

const setupUIScaling = () => {
  const urlParams = new URLSearchParams(window.location.search);
  const rawScale = urlParams.get('scale');
  if (!rawScale) return;
  uiScale = parseFloat(rawScale) || 1;

  const scaleTargets = [
    { selector: '#logo-container img.logo', origin: null },
    { selector: '#session-name', origin: null },
    { selector: '#top-container', origin: 'top right' },
    { selector: '#ap-container', origin: 'top left' },
    { selector: '#qr-code', origin: 'bottom left' },
    { selector: '#up-next', origin: 'bottom right' },
    { selector: '#dvd', origin: null },
    { selector: '#your-score-text', origin: null },
    { selector: '#score-number-text', origin: null },
    { selector: '#score-review-text', origin: null },
    { selector: '#splash-notification', origin: 'top left' },
    { selector: '#clock', origin: 'top left' },
  ];

  scaleTargets.forEach(({ selector, origin }) => {
    const el = document.querySelector(selector);
    if (el) {
      el.style.transform = `scale(${uiScale})`;
      if (origin) el.style.transformOrigin = origin;
    }
  });
}

// Document ready procedures

$(function () {
  // Setup various features and listeners
  setupUIScaling();
  if (PikaraokeConfig.showSplashClock) startClock();
  setupScreensaver();
  setupOverlayMenus();
  setupVideoPlayer();
  setupBackgroundMusicPlayer();

  // Handle browser compatibility
  handleUnsupportedBrowser();
  testAutoplayCapability();
});


// Setup sockets and recovery outside of document ready to prevent race conditions
setupSocketEvents();
handleSocketRecovery();

// Fallback: if socket connected before listeners were attached, register now
if (socket.connected) {
  console.log('Socket already connected, registering splash...');
  socket.emit("register_splash", {protocol:2});
}
