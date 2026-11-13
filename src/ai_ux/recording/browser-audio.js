/* Passive taps on the page's existing Web Audio graph. No microphone is requested here. */
(() => {
  if (window !== window.top || window.__uxAudioRecorder) return;
  const settings = __UX_AUDIO_SETTINGS__;
  const nativeConnect = AudioNode.prototype.connect;
  const nativeDisconnect = AudioNode.prototype.disconnect;
  const nativeStreamSource = AudioContext.prototype.createMediaStreamSource;
  const send = window[settings.binding];
  const contexts = new WeakMap(), taps = [], streams = new WeakSet();
  const pending = new Set(), windows = [];
  const documentId = Math.random().toString(36).slice(2);
  let active = true, sequence = 0, bytes = 0, reportedLimit = false;
  const wall = () => performance.timeOrigin + performance.now();
  function post(value) {
    if (pending.size >= 32) {
      if (!reportedLimit) { reportedLimit = true; void send({type:'warning',code:'audio_capture_backpressure'}).catch(()=>{}); }
      return;
    }
    const promise = send(value).catch(()=>{}).finally(()=>pending.delete(promise));
    pending.add(promise);
  }
  function makeTap(ctx, kind, channels) {
    if (taps.length >= 8) throw new Error('audio_capture_stream_limit');
    const processor = ctx.createScriptProcessor(2048, channels, channels);
    const silent = ctx.createGain(); silent.gain.value = 0;
    nativeConnect.call(processor, silent); nativeConnect.call(silent, ctx.destination);
    const tap = {ctx, processor, silent, kind, channels, id:documentId+'-'+(++sequence), anchor:null};
    ctx.addEventListener('statechange', () => { tap.anchor = null; });
    processor.onaudioprocess = event => {
      if (!active) return;
      const frames = event.inputBuffer.length, rate = event.inputBuffer.sampleRate;
      // Chromium's ScriptProcessor input precedes its output playback time by two buffers.
      // The recorder is Chromium-only; the offset is covered by a generated-tone regression.
      const contextStart = event.playbackTime - 2 * frames / rate;
      if (tap.anchor === null) {
        const stamp = kind === 'output' ? ctx.getOutputTimestamp?.() : null;
        tap.clock = stamp?.performanceTime > 0 ? 'audio_context_output_timestamp' : 'audio_context_render_clock';
        tap.anchor = stamp?.performanceTime > 0
          ? performance.timeOrigin + stamp.performanceTime - stamp.contextTime * 1000
          : wall() - ctx.currentTime * 1000;
      }
      const start = tap.anchor + contextStart * 1000;
      const pcm = new Int16Array(frames * channels);
      let peak = 0;
      for (let i=0; i<frames; i++) {
        const at = start + i * 1000 / rate;
        const allowed = kind !== 'input' || windows.some(w => at >= w.start && at <= w.end);
        for (let ch=0; ch<channels; ch++) {
          const samples = event.inputBuffer.getChannelData(Math.min(ch,event.inputBuffer.numberOfChannels-1));
          const sample = allowed ? Math.max(-1,Math.min(1,samples[i])) : 0;
          peak = Math.max(peak,Math.abs(sample));
          pcm[i*channels+ch] = Math.round(sample * 32767);
        }
      }
      if (peak < 1/32767) return;
      bytes += pcm.byteLength;
      if (bytes > settings.maxBytes) {
        if (!reportedLimit) { reportedLimit = true; post({type:'warning',code:'audio_capture_byte_limit'}); }
        return;
      }
      const encoded = btoa(String.fromCharCode(...new Uint8Array(pcm.buffer)));
      post({type:'pcm',id:tap.id,kind,rate,channels,epoch_ms:start,frames,pcm:encoded,
            uncertainty_ms:2*frames*1000/rate,clock:tap.clock});
    };
    taps.push(tap);
    post({type:'stream',id:tap.id,kind,rate:ctx.sampleRate,channels});
    return tap;
  }
  function output(ctx) {
    let tap = contexts.get(ctx);
    if (!tap) { tap = makeTap(ctx,'output',2); contexts.set(ctx,tap); }
    return tap;
  }
  AudioNode.prototype.connect = function(destination, ...args) {
    const result = nativeConnect.call(this,destination,...args);
    if (active && this.context instanceof AudioContext && destination === this.context.destination) {
      try { nativeConnect.call(this,output(this.context).processor,...args); }
      catch { post({type:'warning',code:'audio_output_tap_failed'}); }
    }
    return result;
  };
  AudioNode.prototype.disconnect = function(destination,...args) {
    const result = arguments.length ? nativeDisconnect.call(this,destination,...args) : nativeDisconnect.call(this);
    if (destination === this.context.destination) {
      const tap = contexts.get(this.context);
      if (tap) { try { nativeDisconnect.call(this,tap.processor,...args); } catch {} }
    }
    return result;
  };
  if (settings.input && navigator.mediaDevices?.getUserMedia) {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      const stream = await original(constraints);
      if (stream.getAudioTracks().length) streams.add(stream);
      return stream;
    };
    AudioContext.prototype.createMediaStreamSource = function(stream) {
      const source = nativeStreamSource.call(this,stream);
      if (active && streams.has(stream)) {
        try { nativeConnect.call(source,makeTap(this,'input',1).processor); }
        catch { post({type:'warning',code:'audio_input_tap_failed'}); }
      }
      return source;
    };
  }
  // Media elements and speechSynthesis outside Web Audio are explicitly not covered.
  document.addEventListener('play', event => {
    if (event.target instanceof HTMLMediaElement && !event.target.muted)
      post({type:'warning',code:'html_media_audio_not_captured'});
  }, true);
  window.__uxAudioRecorder = {
    beginInput() { windows.push({start:wall(),end:Infinity}); },
    endInput() { const current=windows.at(-1); if(current)current.end=wall(); },
    async stop() {
      const current=windows.at(-1);if(current&&current.end===Infinity)current.end=wall();
      await new Promise(resolve=>setTimeout(resolve,120));
      active=false;
      for (const tap of taps) {
        tap.processor.onaudioprocess=null;
        try { nativeDisconnect.call(tap.processor); nativeDisconnect.call(tap.silent); } catch {}
      }
      await Promise.all([...pending]);
      return {streams:taps.length};
    }
  };
})();
