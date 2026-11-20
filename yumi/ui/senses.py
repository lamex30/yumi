"""Her voice, ears, wake word and the minds she asks (a mixin for App)."""
import sys
import threading
import time

from gi.repository import GLib

from yumi import addressing, chat, config, ears, memory, music, notes, system, tts, voice


class SensesMixin:
    """Talking, listening, the wake loop, music and the questions she asks in the background."""

    DUCK_LEVEL = 30                 # percent, while the ears wait for the phrase after her name
    DUCK_BACK_MS = 12000            # the loudness comes back this long after the name went by

    # ---- her voice: 8-bit blips, or a real one from a local Silero (yumi/tts.py)
    def _voice_state(self):
        if not hasattr(self, 'voice_queue'):
            self.voice_queue = []
        if not hasattr(self, 'voice_streaming'):
            self.voice_streaming = False
        if not hasattr(self, 'voice_pending'):
            self.voice_pending = 0
        if not hasattr(self, '_voice_seq'):
            self._voice_seq = 0
        if not hasattr(self, '_voice_next'):
            self._voice_next = 1
        if not hasattr(self, '_voice_ready'):
            self._voice_ready = {}

    def _voice_blips(self, text):
        """offsets for the blips of one segment, like voice.render expects"""
        from yumi.brain import reveal_times
        times, _ = reveal_times(text)
        return [t for t, ch in zip(times, text) if ch.isalnum()]

    def speak(self, blips, bid, end_pitch, volume=0.5, text=''):
        self.hush()
        self._voice_state()
        self.voice_queue.clear()
        self.voice_pending = 0
        self.voice_streaming = False
        self._voice_seq = 0
        self._voice_next = 1
        self._voice_ready.clear()
        self.voice_id = bid
        if not blips and not text:
            return
        path = config.CACHE_DIR / f'voice{bid % 3}.wav'
        real = bool(text) and self.settings.get('voice_kind') == 'tts'

        def work():
            p = tts.speak(text, volume) if real else None
            if real and not p:
                self.warn_tts()               # not set up or failed: the blips speak instead
            if not p:
                try:
                    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    voice.render(blips, path, seed=bid, end_pitch=end_pitch, volume=volume)
                except OSError:
                    return
                p = path
            GLib.idle_add(lambda: self.voice_start(p, bid) and False)
        threading.Thread(target=work, daemon=True).start()

    def speak_stream(self, text, bid, volume=0.5):
        """a piece of a streaming answer: synthesize it and play it in turn"""
        self._voice_state()
        if not self.voice_streaming or self.voice_id != bid:
            self.hush()
            self.voice_id = bid
            self.voice_streaming = True
        self._voice_seq += 1
        seq = self._voice_seq
        path = config.CACHE_DIR / f'voice{bid % 5}-{seq}.wav'

        def work():
            p = tts.speak(text, volume)
            if not p:
                self.warn_tts()
                try:
                    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    blips = self._voice_blips(text)
                    voice.render(blips, path, seed=bid + seq,
                                 end_pitch=1 if text.rstrip().endswith('?') else 0,
                                 volume=volume)
                    p = path
                except Exception:
                    p = None
            GLib.idle_add(lambda s=seq, pp=p, b=bid: self.voice_stream_ready(pp, b, s) and False)
        threading.Thread(target=work, daemon=True).start()

    def speak_stream_blip(self, blips, bid, end_pitch, volume=0.5):
        """a piece of a streaming answer in blips: render it and play it in turn"""
        self._voice_state()
        if not self.voice_streaming or self.voice_id != bid:
            self.hush()
            self.voice_id = bid
            self.voice_streaming = True
        self._voice_seq += 1
        seq = self._voice_seq
        path = config.CACHE_DIR / f'voice{bid % 5}-{seq}.wav'

        def work():
            try:
                config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                voice.render(blips, path, seed=bid + seq, end_pitch=end_pitch, volume=volume)
                p = path
            except OSError:
                p = None
            GLib.idle_add(lambda s=seq, pp=p, b=bid: self.voice_stream_ready(pp, b, s) and False)
        threading.Thread(target=work, daemon=True).start()

    def voice_stream_ready(self, path, bid, seq=None):
        """a streaming segment is ready: queue it in order when it is its turn"""
        self._voice_state()
        if bid != self.voice_id:
            return False
        b = self.brain
        # keep the bubble alive while this piece waits its turn: TTS can take longer
        # than the typed text, and an expired bubble would drop the last segment
        if path and b.bubble and b.bubble.get('id') == bid:
            secs = tts.wav_len(path)
            if secs:
                b.bubble['until'] = max(b.bubble['until'], b.now + secs + 3.0)
        if seq is None:
            if path:
                self.voice_queue.append(path)
            self._voice_pump()
            return False
        self._voice_ready[seq] = path
        while self._voice_next in self._voice_ready:
            p = self._voice_ready.pop(self._voice_next)
            self._voice_next += 1
            if p:
                self.voice_queue.append(p)
        self._voice_pump()
        return False

    def _voice_pump(self):
        """play the next queued segment when nothing is sounding"""
        self._voice_state()
        if self.talking():
            return
        b = self.brain
        while self.voice_queue:
            path = self.voice_queue.pop(0)
            if self.voice_id is None or not b.bubble or b.bubble.get('id') != self.voice_id:
                continue
            secs = tts.wav_len(path)
            if secs:
                b.bubble['until'] = max(b.bubble['until'], b.now + secs + 1.2)
            self.voice_proc = system.play_file(path)
            if self.voice_proc:
                return

    def voice_stream_done(self, bid):
        """the mind has no more words for this answer"""
        self._voice_state()
        if bid == self.voice_id:
            self.voice_streaming = False
            self._voice_pump()

    def warn_tts(self):
        """once, into the log: why the real voice did not happen"""
        first = not getattr(self, '_tts_warned', False)
        self._tts_warned = True
        if first:
            reason = getattr(tts, 'last_error', '') or 'the voice daemon did not answer'
            print(f'yumi: her real voice did not work ({reason}); '
                  'the 8-bit blips speak instead', file=sys.stderr)
        return first

    def voice_start(self, path, bid):
        b = self.brain
        if bid == self.voice_id and b.bubble and b.bubble.get('id') == bid:
            secs = tts.wav_len(path)
            if secs:                          # the voice may be longer than the typed words
                b.bubble['until'] = max(b.bubble['until'], b.now + secs + 1.2)
            self.voice_proc = system.play_file(path)

    def play_voice_file(self, path):
        """play a ready WAV out loud: the panel's «listen to this voice» uses it
        (the usual speech path would go through her blips and bubble)"""
        self.hush()
        self.voice_preview = True
        self.voice_proc = system.play_file(path)
        if not self.voice_proc:
            self.voice_preview = False
        return bool(self.voice_proc)

    def hush(self):
        self._voice_state()
        self.voice_preview = False
        self.voice_queue.clear()
        self.voice_pending = 0
        self.voice_streaming = False
        self._voice_seq = 0
        self._voice_next = 1
        self._voice_ready.clear()
        p, self.voice_proc = self.voice_proc, None
        try:
            if p:
                p.terminate()
        except (OSError, AttributeError):
            pass

    # ---- her ears and her mind: a local Vosk hears (yumi/ears.py), a local Ollama answers (yumi/chat.py)
    def listen_ears(self, secs):
        """one listen: the daemon records the mic and recognizes it, in its own thread.
        The mic opens only when she is done talking: her own line must not be heard"""
        def work():
            b = self.brain
            for _ in range(80):             # at most ~8 s: her line is not that long
                if not b.listening:
                    return                  # clicked again while she was talking: called off
                if b.now >= b.talk_until and not self.talking():
                    break
                time.sleep(0.1)
            if not b.listening:
                return
            self.hush()                     # still sounding after the wait: she gives way to you
            ears.unmute()                   # her voice is gone: the ears open for you at once
            res = ears.listen(lang=self.settings.get('speech_lang', 'en'), secs=secs,
                              size=self.settings.get('stt_size', 'small'),
                              mic=self.settings.get('stt_mic'),
                              engine=self.settings.get('stt_engine'))
            text = res.get('text') if isinstance(res, dict) else res
            vtxt = res.get('vosk', '') if isinstance(res, dict) else ''
            GLib.idle_add(lambda t=text, v=vtxt: self.ev(lambda b: b.heard(t, v)) and False)
        threading.Thread(target=work, daemon=True).start()

    def talking(self):
        """her voice is sounding at this very moment"""
        p = self.voice_proc
        return bool(p) and p.poll() is None

    def duck_begin(self):
        """her name was heard: the sounds duck so the ears hear the phrase.
        The loudness before the duck is remembered and comes back in a while,
        unless you set the volume yourself — then the new level stays"""
        if not self.settings.get('name_duck'):
            return
        if self.duck_restore:
            GLib.source_remove(self.duck_restore)
        self.duck_restore = GLib.timeout_add(self.DUCK_BACK_MS, self.duck_end)
        if self.duck_saved is not None:
            return                      # already low: the first loudness is the one to keep
        try:
            saved = music.read_volume()
            if saved is None:
                print('yumi: the duck could not read the volume, it stays as it is')
                return
            self.duck_saved = saved
            music.system_volume(f'volume {min(saved, self.DUCK_LEVEL)}')
        except Exception:
            self.duck_saved = None

    def duck_keep(self):
        """a volume command went through: the level she just named is the one that stays"""
        if self.duck_restore:
            GLib.source_remove(self.duck_restore)
        self.duck_restore = None
        self.duck_saved = None

    def duck_end(self):
        """the phrase is over: the loudness you had comes back"""
        self.duck_restore = None
        saved, self.duck_saved = self.duck_saved, None
        if saved is not None:
            try:
                music.system_volume(f'volume {saved}')
            except Exception:
                pass
        return False

    def self_mute(self, secs):
        """her ears close while she speaks (system.play_file keeps the mute alive only
        while the sound plays): a click-listen waits her out anyway, and the wake's
        recorder drops the wake logic until the window runs out — she cannot wake to
        her own words, with or without the echo canceller; a shout of «стоп» gets
        through (the daemon judges the phrases it hears for a stop and nothing else)
        (stt_selfmute)"""
        if not self.settings.get('stt_selfmute', True):
            return
        threading.Thread(target=ears.mute, args=(secs,), daemon=True).start()

    def wake_hints(self, b):
        """the user's own phrases and keywords from actions.json: the ears run in
        another process, so a custom button's wish is passed into the wake router
        (yumi/addressing.py) or the fallback would not know it"""
        return addressing.hints_from(getattr(b, 'custom', None))

    def wake_set(self, on):
        """the wake word: while it is on, a loop keeps the daemon listening for her name
        and, the moment it passes by, the phrase that follows goes straight to her mind"""
        self.wake_on = bool(on)
        if not self.wake_on:
            if self.wake_thread:            # a /wake may still be waiting: let it off the mic
                threading.Thread(target=ears.stop, daemon=True).start()
            return
        if self.wake_thread:
            return

        def work():
            b = self.brain
            while self.wake_on:
                time.sleep(0.15)
                if b.listening:
                    continue            # the mic is lent to a click-listen: not ours now
                res = ears.wake(lang=self.settings.get('speech_lang', 'en'),
                                word=self.settings.get('wake_word', 'юми'), secs=120,
                                size=self.settings.get('stt_size', 'small'),
                                mic=self.settings.get('stt_mic'),
                                sensitivity=self.settings.get('wake_sensitivity', 'normal'),
                                confirm=self.settings.get('wake_confirm', True),
                                model_size=self.settings.get('wake_model_size', 'small'),
                                judge_log=self.settings.get('wake_judge_log', True),
                                engine=self.settings.get('stt_engine'),
                                oww_ear=self.settings.get('wake_ear', 'auto'),
                                hints=self.wake_hints(b))
                if not self.wake_on or not res:
                    continue            # called off or the ears are away
                if res.get('stop'):
                    # «стоп» overrides everything: even mid-word, asleep or thinking
                    GLib.idle_add(lambda: self.ev(lambda b: b.stop_all()) and False)
                    continue
                if not res.get('woke'):
                    continue            # quiet: nobody called her name
                tail = res.get('text') or ''
                alt = res.get('alt') or ''  # the stop ear's reading, the other ear
                fb = bool(res.get('fallback'))   # the name from vosk, not from the trained ear
                GLib.idle_add(self.duck_begin)   # her name passed: quiet now, whatever comes
                if b.thinking or b.listening:
                    continue            # she got busy in those seconds: the word is spent
                # the name was heard: the moth that marks where she hides becomes the
                # girl — asleep she wakes, hidden she comes out, right now, while the
                # phrase (or the wait for it) is still going
                GLib.idle_add(lambda: self.ev(lambda br: br.wake_form()) and False)
                if b.now < b.talk_until or self.talking():
                    self.hush()         # her own line was still sounding: it dies, your turn now
                if tail:
                    # name + text already recognised: pass the whole phrase straight to the brain
                    GLib.idle_add(lambda t=tail, a=alt, f=fb:
                                  self.ev(lambda br: br.wake_up(t, a, f)) and False)
                else:
                    # «юми» без текста: ухо openWakeWord только имя ловит,
                    # команда за ней теряется — слушаем фразу, которая пошла после имени
                    cmd = ears.listen(lang=self.settings.get('speech_lang', 'ru'),
                                      secs=15,
                                      size=self.settings.get('stt_size', 'small'),
                                      mic=self.settings.get('stt_mic'),
                                      engine=self.settings.get('stt_engine'))
                    if cmd:
                        said = cmd.get('text') or ''
                        vtxt = cmd.get('vosk') or ''   # the other reading, as on the click-listen
                        if said:
                            GLib.idle_add(lambda t=said, v=vtxt: self.ev(lambda br: br.wake_up(t, v)) and False)
                        else:
                            # silence after the name: she asks what you wanted
                            GLib.idle_add(lambda: self.ev(lambda br: br.wake_up('')) and False)
                    else:
                        # ears unavailable: fall back to asking
                        GLib.idle_add(lambda: self.ev(lambda br: br.wake_up('')) and False)

        self.wake_thread = threading.Thread(target=work, daemon=True)
        self.wake_thread.start()

    def wake_pause(self, timeout=6.0):
        """give the microphone to a one-off test from the panel: stop the wake
        loop and wait for its daemon window to close (True if it was running)"""
        was_on = bool(self.wake_on)
        self.wake_set(False)
        t = self.wake_thread
        if t and t.is_alive():
            t.join(timeout)
        if t and not t.is_alive():
            self.wake_thread = None
        return was_on

    def wake_resume(self, was_on):
        """the panel's test is over: the wake loop takes the microphone back"""
        if not was_on:
            return
        if not (self.wake_thread and self.wake_thread.is_alive()):
            self.wake_thread = None
        self.wake_on = True
        self.wake_set(True)

    def tap_set(self, on):
        """the ears' diary: while it is on, the daemon records everything the ears hear
        into /tmp/yumi (the sound and the words), in its own thread beside her work"""
        def work():
            ears.tap(bool(on), lang=self.settings.get('speech_lang', 'en'),
                     size=self.settings.get('stt_size', 'small'),
                     mic=self.settings.get('stt_mic'))
        threading.Thread(target=work, daemon=True).start()

    def _chat_reply_remember(self, reply):
        """a streamed reply finished; memory was already saved by the worker"""
        return False

    def chat_ask(self, history):
        """the question is away to Ollama; the answer comes back into her bubble"""
        def work():
            s = self.settings
            lang = s.get('speech_lang', 'en')
            # the copy the worker walks: `history` is the caller's list, and an
            # assignment to it here would make every read of it a crash
            # (UnboundLocalError — every question died before the answer)
            hist = list(history)
            user_text = str(hist[-1].get('content') or '')   # the question just asked
            # the diary: older lines about the same thing join the question, the
            # talks about other things stay out of the way (yumi/memory.py)
            if s.get('chat_memory', True):
                try:
                    note = chat.recalled_note(
                        memory.recall(user_text, s, skip_recent=len(hist)), lang)
                except Exception:
                    note = ''
                if note:
                    hist.append({'role': 'system', 'content': note})
            sassy = bool(s.get('sassy'))
            prompt = chat.system_prompt(lang, sassy)
            full = [{'role': 'system', 'content': prompt}] + hist
            tag = memory.TAG_SASSY if sassy else None
            use_stream = bool(s.get('chat_stream', True))

            if not use_stream:
                reply = chat.ask(s, full)
                if s.get('chat_memory', True) and bool(s.get('sassy')) == sassy:
                    try:
                        memory.remember('user', user_text, s, tag=tag)
                        if reply:
                            memory.remember('assistant', reply, s, tag=tag)
                    except Exception:
                        pass
                GLib.idle_add(lambda: self.ev(lambda b: b.replied(reply)) and False)
                return

            GLib.idle_add(lambda: self.ev(lambda b: b.reply_stream_begin()) and False)
            reply_parts = []
            try:
                for chunk in chat.ask_stream(s, full):
                    if not chunk:
                        continue
                    reply_parts.append(chunk)
                    GLib.idle_add(lambda c=chunk: self.ev(lambda b: b.reply_stream_chunk(c)) and False)
            except Exception:
                pass

            reply = ' '.join(''.join(reply_parts).split())
            if s.get('chat_memory', True) and bool(s.get('sassy')) == sassy and reply:
                try:
                    memory.remember('user', user_text, s, tag=tag)
                    memory.remember('assistant', reply, s, tag=tag)
                except Exception:
                    pass

            if reply:
                GLib.idle_add(lambda: self.ev(lambda b: b.reply_stream_end()) and False)
            else:
                GLib.idle_add(lambda: self.ev(lambda b: b.reply_stream_fail()) and False)
        threading.Thread(target=work, daemon=True).start()

    def weather_ask(self, ask):
        """the weather question is away to the clouds; the peek comes back into her bubble"""
        def work():
            lang = self.settings.get('speech_lang', 'en')
            reply = chat.weather_report(ask.get('where', ''), lang, ask.get('when', 'today'))
            GLib.idle_add(lambda: self.ev(lambda b: b.said_weather(reply)) and False)
        threading.Thread(target=work, daemon=True).start()

    def music_who(self):
        """the «кто играет» question: the player is asked in its own thread
        (playerctl and mpc take a moment), the names go back into her bubble"""
        def work():
            playing = music.now_playing()
            GLib.idle_add(lambda: self.ev(lambda b: b.said_now_playing(playing)) and False)
        threading.Thread(target=work, daemon=True).start()

    def music_search(self, payload):
        """«включи песню ...»: the libraries are asked away from her main thread
        (a search walks the network and her folders, then the queue is built),
        the report goes back into her bubble"""
        def work():
            music.apply_settings(self.settings)
            report = music.play_query(payload.get('kind', 'song'),
                                      payload.get('query', ''), self.settings)
            GLib.idle_add(lambda: self.ev(lambda b: b.said_music_found(report)) and False)
        threading.Thread(target=work, daemon=True).start()

    def hourly_ask(self, payload):
        """her mind is asked for a funny line about this hour; the reply goes back to the brain"""
        def work():
            lang = payload.get('lang', 'en')
            hour = int(payload.get('hour', time.localtime().tm_hour))
            joke = chat.hourly_joke(self.settings, hour, lang)
            GLib.idle_add(lambda: self.ev(lambda b: b.hour_joked({'joke': joke})) and False)
        threading.Thread(target=work, daemon=True).start()

    def joke_ask(self, payload):
        """her mind is asked for a joke; the request and the joke go into the
        diary tagged (the vectors too), and in «Дерзкая 18+» the fourth one in a
        row gets a model-written brush-off instead"""
        def work():
            s = self.settings
            lang = payload.get('lang', 'en')
            sassy = bool(s.get('sassy'))
            tag = memory.TAG_SASSY_JOKE if sassy else memory.TAG_JOKE
            asked = str(payload.get('text') or '').strip() or (
                'анекдот' if str(lang) == 'ru' else 'a joke')
            # how many jokes in a row this one makes: the diary holds the ones
            # already asked, the current request has not been written down yet
            try:
                streak = memory.joke_streak(tag) + 1
            except Exception:
                streak = 1
            tired = sassy and streak >= chat.JOKE_TIRED_AFTER
            reply = chat.joke_tired(s, lang, streak) if tired else chat.joke(s, lang)
            GLib.idle_add(lambda: self.ev(lambda b: b.joked(reply, tired)) and False)
            # the request and the joke go into the diary tagged, with their
            # vectors (the jokes are written even when the resting chats are
            # not: the run is counted from them, and the mode's lines must be
            # found and wiped when «Дерзкая 18+» goes off)
            if bool(s.get('sassy')) != sassy:
                return                  # the mode changed mid-joke: leave the diary clean
            try:
                memory.remember('user', asked, s, tag=tag)
                if reply:
                    memory.remember('assistant', reply, s, tag=tag)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def note_voice(self, wish):
        """the notes' answer to a spoken phrase: a new note goes into the book, a
        search reads the closest ones back; both away from the GTK thread (the
        embedder may be slow or missing)"""
        action = str((wish or {}).get('action') or '')
        text = str((wish or {}).get('text') or '')
        query = str((wish or {}).get('query') or '')

        def work():
            try:
                if action == 'add':
                    note = notes.add(text, self.settings, source='voice')
                    if self.web:
                        self.web.publish('notes', {'action': 'add', 'note': note})
                    GLib.idle_add(lambda: self.ev(lambda b: b.note_saved(note)) and False)
                    return
                if action == 'last':
                    items = notes.recent(1)
                    if items:
                        note = items[0]
                        try:
                            summary = chat.summarize(
                                self.settings, note.get('title'), note.get('text'),
                                self.settings.get('speech_lang', 'en'))
                        except Exception:
                            summary = ''
                        items = [dict(note, summary=summary or notes.brief(note.get('text')))]
                elif action == 'list':
                    items = notes.recent(5)
                else:
                    items = notes.search(query, self.settings, limit=5)
                payload = {'action': action, 'items': items, 'query': query}
                GLib.idle_add(lambda: self.ev(lambda b: b.note_found(payload)) and False)
            except Exception:
                GLib.idle_add(lambda: self.ev(lambda b: b.note_failed()) and False)
        threading.Thread(target=work, daemon=True).start()

    def fetch_chat_models(self):
        """the installed Ollama models, for Settings → Her mind → Answer model"""
        def work():
            names = chat.models(self.settings)
            GLib.idle_add(lambda: self.ev(lambda b: b.chat_models_loaded(names)) and False)
        threading.Thread(target=work, daemon=True).start()

    def fetch_mics(self):
        """the microphones her ears can hear, for Settings → Her ears → Microphone"""
        def work():
            mics = ears.mics()
            GLib.idle_add(lambda: self.ev(lambda b: b.mics_loaded(mics)) and False)
        threading.Thread(target=work, daemon=True).start()
