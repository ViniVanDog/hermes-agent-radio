/* Browser playback, audio spectrogram and tuning timeline. No microphone access. */
(() => {
  const listen = document.querySelector('#listen-scanner');
  const message = document.querySelector('#audio-stream-status');
  const spectrum = document.querySelector('#scanner-spectrogram');
  const timeline = document.querySelector('#scanner-timeline');
  const position = document.querySelector('#timeline-position');
  const record = document.querySelector('#scan-record');
  const limit = document.querySelector('#scan-record-limit');
  const volume = document.querySelector('#scanner-volume');
  const WINDOW = 45, FRAME = 656, SAMPLES = 320, RATE = 16000;
  let player = null, initialized = false, available = false;
  let transitions = [], shownKey = '', lastDraw = 0, animation = null;
  let pixelRemainder = 0;

  function resize() {
    const width = Math.max(280, Math.round(spectrum.getBoundingClientRect().width));
    if (spectrum.width !== width) {
      spectrum.width = timeline.width = width;
      spectrum.height = 200; timeline.height = 54;
    }
  }
  window.addEventListener('resize', resize);

  function label(entry) {
    if (!entry || !entry.frequency) return 'Preparing';
    const frequency = (entry.frequency / 1e6).toFixed(4) + ' MHz';
    return entry.flags & 1 ? 'Tuning → ' + frequency : frequency;
  }
  function clock(epoch) {
    return new Date(epoch * 1000).toLocaleTimeString();
  }
  function color(value) {
    const stops = [[0, [16,21,29]], [.25, [28,55,110]], [.5, [24,157,164]],
                   [.75, [231,207,58]], [1, [255,87,72]]];
    const level = Math.max(0, Math.min(1, value));
    for (let i = 1; i < stops.length; i++) {
      if (level <= stops[i][0]) {
        const amount = (level - stops[i-1][0]) / (stops[i][0] - stops[i-1][0]);
        return stops[i-1][1].map((component, j) => Math.round(component +
          (stops[i][1][j] - component) * amount));
      }
    }
    return stops.at(-1)[1];
  }

  function draw() {
    animation = requestAnimationFrame(draw);
    if (!player) return;
    const now = player.context.currentTime;
    if (now - lastDraw < .045) return;
    resize();
    const width = spectrum.width, height = spectrum.height;
    const ctx = spectrum.getContext('2d');
    const advance = (now - lastDraw) * width / WINDOW + pixelRemainder;
    const delta = Math.floor(advance);
    pixelRemainder = advance - delta;
    lastDraw = now;
    if (delta >= width) ctx.clearRect(0, 0, width, height);
    else if (delta > 0) ctx.drawImage(spectrum, -delta, 0);
    const frequencies = new Float32Array(player.analyser.frequencyBinCount);
    player.analyser.getFloatFrequencyData(frequencies);
    const column = ctx.createImageData(1, height);
    for (let y = 0; y < height; y++) {
      const hz = (1 - y / (height - 1)) * 8000;
      const bin = Math.min(frequencies.length - 1, Math.round(hz *
        player.analyser.fftSize / player.context.sampleRate));
      const rgb = color((frequencies[bin] + 95) / 70);
      column.data.set([...rgb, 255], y * 4);
    }
    for (let x = Math.max(0, width - delta); x < width; x++) ctx.putImageData(column, x, 0);
    const current = transitions.filter(entry => entry.at <= now).at(-1);
    const key = current ? current.frequency + ':' + (current.flags & 1) : '';
    if (key && key !== shownKey) {
      ctx.strokeStyle = '#f1f5f9'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(width - 1, 0); ctx.lineTo(width - 1, height); ctx.stroke();
      shownKey = key;
    }
    spectrum.dataset.frames = String(Number(spectrum.dataset.frames || 0) + 1);
    const bar = timeline.getContext('2d');
    bar.fillStyle = '#10151d'; bar.fillRect(0, 0, width, 54);
    bar.font = '11px system-ui';
    const oldest = now - WINDOW;
    for (let i = 0; i < transitions.length; i++) {
      const entry = transitions[i];
      const end = Math.min(now, transitions[i+1]?.at ?? now);
      if (end < oldest || entry.at > now) continue;
      const left = Math.max(0, (entry.at - oldest) / WINDOW * width);
      const right = Math.min(width, (end - oldest) / WINDOW * width);
      bar.fillStyle = entry.flags & 1 ? '#526071' :
        `hsl(${(Math.round(entry.frequency / 12500) * 47) % 360} 40% 33%)`;
      bar.fillRect(left, 0, Math.max(1, right - left), 31);
      bar.fillStyle = '#eef4fa';
      if (right - left > 78) bar.fillText(label(entry), left + 4, 19, right - left - 8);
      bar.fillRect(left, 0, 1, 32);
      if (right - left > 64) bar.fillText(clock(entry.epoch), left + 4, 47);
    }
    while (transitions.length > 1 && transitions[1].at < oldest) transitions.shift();
    document.querySelector('#frequency-transitions').textContent = transitions
      .filter(entry => entry.at <= now).slice(-4)
      .map(entry => clock(entry.epoch) + ' · ' + label(entry)).join(' → ');
    if (current) position.textContent = clock(current.epoch + now - current.at) +
      ' · ' + label(current) + ' · last 45 seconds';
  }

  timeline.addEventListener('mousemove', event => {
    if (!player) return;
    const box = timeline.getBoundingClientRect();
    const at = player.context.currentTime - WINDOW + (event.clientX - box.left) / box.width * WINDOW;
    const entry = transitions.filter(item => item.at <= at).at(-1);
    if (entry) position.textContent = clock(entry.epoch + at - entry.at) + ' · ' + label(entry);
  });

  async function stopAudio(text = 'Listening stopped.') {
    const previous = player; player = null;
    if (previous) {
      previous.abort.abort();
      await previous.context.close().catch(() => {});
    }
    if (animation !== null) cancelAnimationFrame(animation);
    animation = null;
    listen.textContent = 'Listen and visualize';
    listen.disabled = !available;
    message.textContent = text;
  }

  async function startAudio() {
    if (player) return stopAudio();
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) { message.textContent = 'This browser does not support Web Audio playback.'; return; }
    const context = new Context({sampleRate: RATE});
    const analyser = context.createAnalyser(); analyser.fftSize = 2048;
    const gain = context.createGain(); gain.gain.value = Number(volume.value) / 100;
    analyser.connect(gain); gain.connect(context.destination);
    const state = {context, analyser, gain, abort: new AbortController(), next: 0};
    player = state; transitions = []; shownKey = ''; lastDraw = context.currentTime; pixelRemainder = 0;
    listen.textContent = 'Stop listening'; message.textContent = 'Connecting to audio…';
    try {
      await context.resume();
      const response = await fetch('/api/scanner/audio', {cache: 'no-store', signal: state.abort.signal});
      if (!response.ok) throw Error('Audio unavailable; check that the scanner is running.');
      const reader = response.body.getReader();
      let pending = new Uint8Array(0);
      resize(); animation = requestAnimationFrame(draw);
      message.textContent = 'Live · audio and timeline in sync';
      while (player === state) {
        const {value, done} = await reader.read();
        if (done) break;
        const bytes = new Uint8Array(pending.length + value.length);
        bytes.set(pending); bytes.set(value, pending.length);
        let offset = 0;
        while (bytes.length - offset >= FRAME) {
          const data = new DataView(bytes.buffer, bytes.byteOffset + offset, FRAME);
          const epoch = data.getFloat64(0, true), frequency = data.getUint32(8, true), flags = data.getUint8(12);
          const now = context.currentTime;
          if (state.next < now) state.next = now + .1;
          // Drop excess buffered frames instead of scheduling overlapping
          // playback when a browser or connection briefly stalls.
          if (state.next - now > .6) { offset += FRAME; continue; }
          const buffer = context.createBuffer(1, SAMPLES, RATE);
          const output = buffer.getChannelData(0);
          for (let i = 0; i < SAMPLES; i++) output[i] = data.getInt16(16 + i * 2, true) / 32768;
          const source = context.createBufferSource(); source.buffer = buffer;
          source.connect(analyser); source.start(state.next);
          const previous = transitions.at(-1);
          if (!previous || previous.frequency !== frequency || (previous.flags & 1) !== (flags & 1)) {
            transitions.push({at: state.next, epoch, frequency, flags});
            timeline.dataset.transitions = String(transitions.length);
            timeline.dataset.frequency = String(frequency);
          }
          state.next += SAMPLES / RATE;
          offset += FRAME;
        }
        pending = bytes.slice(offset);
      }
      if (player === state) await stopAudio('Stream ended; profile restored or scanner stopped.');
    } catch (error) {
      if (player === state) await stopAudio(error.name === 'AbortError' ? 'Listening stopped.' : error.message);
    }
  }

  listen.addEventListener('click', startAudio);
  volume.addEventListener('input', () => { if (player) player.gain.gain.value = Number(volume.value) / 100; });
  const saveRecordingPreference = () => {
    if (limit.reportValidity()) scannerAction('recording', {enabled: record.checked, limit_mb: Number(limit.value)});
  };
  record.addEventListener('change', saveRecordingPreference);
  limit.addEventListener('change', saveRecordingPreference);

  document.addEventListener('scanner-status', event => {
    const data = event.detail;
    const audio = data.audio || {};
    available = Boolean(audio.live_available);
    listen.disabled = !available;
    if (!available && player) stopAudio('Scanner stopped; listening ended.');
    if (!initialized) {
      record.checked = data.config.record_audio || false;
      limit.value = data.config.recording_limit_mb || 1024;
      initialized = true;
    }
    document.querySelector('#recording-status').textContent = audio.warning ||
      (audio.recording_active ? 'Recording voice on the host · ' : audio.recording_enabled ? 'Voice capture enabled · ' : 'Capture off · ') +
      (audio.used_mb || 0) + ' MB on the host / limit ' + (audio.limit_mb || 1024) + ' MB. Files are not saved on the VM.';
    const body = document.querySelector('#scanner-recordings'); body.replaceChildren();
    for (const entry of data.recordings || []) {
      const row = document.createElement('tr');
      for (const text of [clock(entry.started_at), Number(entry.frequency_mhz).toFixed(4) + ' MHz', entry.duration_seconds.toFixed(2) + ' s']) {
        const td = document.createElement('td'); td.textContent = text; row.appendChild(td);
      }
      const td = document.createElement('td'), link = document.createElement('a');
      link.href = '/api/scanner/recordings/' + encodeURIComponent(entry.filename);
      link.textContent = 'Download WAV · ' + (entry.bytes / 1024).toFixed(1) + ' KB'; link.download = entry.filename;
      td.appendChild(link); row.appendChild(td); body.appendChild(row);
    }
    if (!body.children.length) {
      const row = document.createElement('tr'), td = document.createElement('td');
      td.colSpan = 4; td.textContent = 'No recordings.'; row.appendChild(td); body.appendChild(row);
    }
  });
  if (typeof scannerPoll === 'function') scannerPoll();
})();
