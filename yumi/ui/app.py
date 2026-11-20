"""Yumi's GTK application: windows, frames, requests, jobs and her colors."""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from yumi import config, ears, files, jobs, memory, music, system, tts
from yumi.brain import Brain
from yumi.phrases import Speech
from yumi.sprites import Meta, palette_delta, palette_from_hue

from .constants import ACTIONS, APP_ID, ASK, CSS
from .overlay import Overlay
from .painter import Painter, load_surfaces
from .senses import SensesMixin
from .webpanel import PanelMixin


class App(PanelMixin, SensesMixin, Gtk.Application):

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.windows = []
        self.active = None
        self.sound_src = None
        self.voice_proc = None          # the player saying her current line
        self.voice_preview = False      # a panel sample: no bubble owns it, keep it playing
        self.failed = False             # this desktop cannot show her
        self.voice_id = None            # which bubble it belongs to
        self.load_gen = 0               # sprite reloads: only the newest one is used
        self.outfit_want = None         # the outfit she wears or is changing into
        self.sound_procs = []
        self.pending = []
        self.duck_saved = None          # the loudness before the duck (the name_duck setting)
        self.duck_restore = None        # the timer that brings the loudness back
        self.form_picking = False       # the file dialog of the button form is open
        self.started = time.time()
        self.web = None                 # the optional browser panel (off by default)
        self.web_sig = None             # (enabled, host, port, session hours) it was started with
        self.web_sync_src = None
        self.web_opened = False
        self.web_report = None          # last (state, detail) printed about the panel
        self._web_music_at = 0.0
        self._web_music = None
        self._web_volume = None
        self._web_bubble = ''
        self._web_status_at = 0.0
        self.web_chat_path = config.STATE_DIR / 'web-chat.json'
        self.web_chat_lock = threading.Lock()
        self.web_chat = self.load_web_chat()
        ask_type = GLib.VariantType.new('s')
        for name in ACTIONS + (ASK,):
            a = Gio.SimpleAction.new(name, ask_type if name == ASK else None)
            a.connect('activate', self.on_action, name)
            self.add_action(a)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self.settings = config.load_settings()
        self.state = config.load_state()
        system.set_mute_hook(self.self_mute)   # her ears close while her voice plays
        scale = int(os.environ.get('YUMI_SCALE') or self.settings.get('scale') or 1)
        self.world = system.World({
            'charger': lambda on: self.ev(lambda b: b.world('charger_on' if on else 'charger_off')),
            'battery_low': lambda pct: self.ev(lambda b: b.world('battery_low', pct=pct)),
            'wifi': lambda ssid: self.ev(lambda b: b.world('wifi_on', ssid=ssid) if ssid else b.world('wifi_off')),
            'dnd': lambda on: self.ev(lambda b: b.set_sleep(on)),
            'music': lambda on: self.ev(lambda b: b.world('music', playing=on)),
            'beat': lambda v: self.ev(lambda b: b.world('beat', level=v)),
            'idle': lambda kind: self.ev(lambda b: b.idle_event(kind)),
            'palette': self.on_palette,
            'caps': self.on_caps,
        })
        self.meta = Meta(config.SPRITES, self.pick_outfit())
        self.outfit_want = self.meta.outfit
        self.painter = Painter(self.meta, scale)
        self.palette = self.effective_palette()
        self.painter.set_palette(self.palette)
        self.painter.surf = load_surfaces(self.meta, palette_delta(self.palette))
        self.speech = Speech(self.settings)
        self.recent = files.Recent(changed=self.kick,
                                   download=lambda p: self.ev(lambda b: b.world('download', file=p.name)),
                                   screenshot=lambda p: self.ev(lambda b: b.world('screenshot', file=p.name)))
        self.brain = Brain(self.meta, self.settings, self.state, self.speech, self.painter.measure,
                           scale=scale, now=time.monotonic(), recent=self.recent.list)
        self.brain.outfit_names = Meta.outfits(config.SPRITES)
        self.wake_on = False             # the wake-word loop is running or not
        self.wake_thread = None
        self.wake_set(bool(self.settings.get('wake_ears')))   # «Юми» works out of the box
        self.tap_set(bool(self.settings.get('tap_ears')))     # the diary, too, if it was asked
        music.apply_settings(self.settings)   # her mpd, or the server's (music_server)
        threading.Thread(target=music.service_start, daemon=True).start()   # mpd lives with her
        if not self.settings.get('sassy'):
            # a crash may have kept the brat's lines after the mode went off:
            # the diary is swept at every start while the mode is off
            threading.Thread(target=memory.clear_tag,
                             args=(memory.TAG_SASSY,), daemon=True).start()
        self.fetch_chat_models()        # her answer model for the settings, in the background
        self.on_caps(initial=True)
        GLib.timeout_add_seconds(3600, lambda: self.apply_outfit() or True)    # the calendar moves on
        self.queue = jobs.Queue(self.job_progress, self.job_done, self.job_start)
        self.ops = []
        self.timer = None
        self.dl_announced = {}          # the model a daemon fetches now: src -> name, told about
        GLib.timeout_add_seconds(4, self.watch_downloads)
        self.hold()                                  # keep running without a mapped window too
        self.web_sync()                              # the browser panel, only if switched on
        GLib.timeout_add(1500, lambda: self.ev(lambda b: b.say('hello', force=True)) and False)

    def pick_outfit(self):
        """the outfit folder to wear now: by the calendar, or the one chosen in the settings"""
        want = self.settings.get('outfit', 'auto')
        name = config.outfit_for() if want == 'auto' else None if want == 'off' else want
        return name if name in Meta.outfits(config.SPRITES) else None

    def apply_outfit(self):
        name = self.pick_outfit()
        if name == self.outfit_want:
            return
        self.outfit_want = name
        self.apply_palette(self.effective_palette(), Meta(config.SPRITES, name))
        self.ev(lambda b: b.set_face('happy', 2.5))

    def close(self):
        """let all her companion services go before she does: the TTS daemon,
        the STT daemon, her music and anything else that outlives her process."""
        self.wake_on = False            # the wake loop stops asking: no ears rise after her
        if self.web:
            self.web.stop()
            self.web = None
            self.web_sig = None
        self.duck_end()                 # the ducked loudness comes back before she goes
        if self.duck_restore:
            GLib.source_remove(self.duck_restore)
            self.duck_restore = None
        tts.close()
        ears.close()
        self.stop_timer_sound()
        try:
            music.service_stop()
        except Exception:
            pass

    def watch_downloads(self):
        """her servants fetch their models on their own (vosk, whisper, silero voices):
        every few seconds she asks them what they pull, so a long quiet download never
        looks like she froze — a bubble and a word when a model's download begins,
        and one more when it lands; the models' first download is the only one told"""
        threading.Thread(target=self.watch_downloads_once, daemon=True).start()
        return True                     # the timer keeps going

    def watch_downloads_once(self):
        """runs off the interface thread: each daemon's answer is a name, '' for
        «alive, fetching nothing» or None for «not running»; a fresh name is told,
        a vanished one is congratulated (only while its daemon is still alive)"""
        now = {}
        for src, state in (('ears', ears.downloading()), ('tts', tts.downloading())):
            if state is not None:       # the daemon answers: it is alive
                now[src] = state
        for src, name in now.items():
            if name and src not in self.dl_announced:
                self.dl_announced[src] = name
                self.ev(lambda b, n=name: b.world('model_dl', model=n))
        for src, name in list(self.dl_announced.items()):
            if src not in now:          # the daemon itself went away: no «ready» words
                del self.dl_announced[src]
            elif now[src] != name:      # the download finished while it still runs
                del self.dl_announced[src]
                self.ev(lambda b, n=name: b.world('model_dl_done', model=n))

    def do_activate(self):
        if not LayerShell.is_supported():
            sys.stderr.write('yumi: this desktop has no wlr-layer-shell (GNOME and X11 are not '
                             'supported yet). She lives on sway, Hyprland, niri, river, Wayfire, '
                             'labwc, KDE Plasma, COSMIC...\n')
            self.failed = True
            self.close()
            self.quit()
            return
        if not self.windows:
            self.sync_windows()
            self.kick()

    # ---- screens
    @staticmethod
    def list_monitors():
        try:
            model = Gdk.Display.get_default().get_monitors()
            return [model.get_item(i) for i in range(model.get_n_items())]
        except Exception:
            return []

    def sync_windows(self):
        """a window per screen (kept in step when screens come and go)"""
        mons = self.list_monitors()
        names = [(m.get_connector() or str(i)) for i, m in enumerate(mons)]
        old = {w.name: w for w in self.windows}
        wins = []
        if not mons:
            wins = self.windows[:1] or [Overlay(self)]
        else:
            for name, mon in zip(names, mons):
                w = old.pop(name, None)
                if w is None:
                    w = Overlay(self, mon)
                wins.append(w)
        for w in old.values():                      # screens that went away
            w.destroy()
        self.windows = wins
        want = self.state.get('monitor')
        target = next((w for w in wins if w.name == want), None) or wins[0]
        if self.active not in wins:
            self.active = None
        if self.active is None:
            self.switch_to(target, remember=False)
        self.brain.monitors = len(wins)
        for w in wins:
            w.present()
        if not getattr(self, '_mon_watch', False) and mons:
            self._mon_watch = True
            try:
                Gdk.Display.get_default().get_monitors().connect(
                    'items-changed', lambda *_a: GLib.idle_add(lambda: self.sync_windows() and False))
            except Exception:
                pass

    def monitor_at(self, gx, gy):
        for w in self.windows:
            if w.ox <= gx < w.ox + w.mw and w.oy <= gy < w.oy + w.mh:
                return w
        return None

    def switch_to(self, win, remember=True):
        """she lives on this screen from now on"""
        if win is self.active:
            return
        self.active = win
        if remember:
            self.state['monitor'] = win.name
            self.state.save()
        w, h = win.area.get_width(), win.area.get_height()
        ok = isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0
        self.brain.resize(w if ok else win.mw, h if ok else win.mh)
        for other in self.windows:
            other.last_region = None
            other.update_input()
            other.area.queue_draw()

    def cycle_monitor(self):
        if len(self.windows) < 2:
            return
        i = self.windows.index(self.active) if self.active in self.windows else -1
        self.switch_to(self.windows[(i + 1) % len(self.windows)])
        self.ev(lambda b: b.go_home())

    def on_action(self, _a, param, name):
        self.do_activate()
        if name == ASK:
            # the phrase goes in where the mic's words go: she wakes, says it back
            # and answers with her voice, as if it had been heard in the room
            phrase = ' '.join(str(param.get_string() if param is not None else '').split())
            if phrase:
                self.ev(lambda b: b.wake_up(phrase))
            return
        if name == 'toggle':
            self.ev(lambda b: b.toggle())
        elif name == 'pipette':
            self.ev(lambda b: (b.pop(True) if b.mode == 'docked' and not b.sleeping else None,
                               b.start_pipette()))
        elif name == 'home':
            self.ev(lambda b: b.go_home())
        elif name == 'monitor':
            self.cycle_monitor()
        elif name == 'recent':
            self.ev(lambda b: b.toggle_pile('recent'))
        elif name == 'sleep':
            self.ev(lambda b: b.requests.append(('dnd', not b.sleeping)))
        elif name == 'web':
            self.web_open()
        elif name == 'quit':
            self.close()
            self.quit()

    # ---- the loop: every event and every frame goes through here
    def ev(self, fn=None):
        if fn:
            fn(self.brain)
        self.kick()

    def kick(self):
        """run a frame now and make sure the timer keeps running at the brain's pace"""
        self.frame()

    def frame(self):
        b = self.brain
        b.update(time.monotonic())
        self.handle_requests()
        if self.voice_proc and not self.talking():
            self.voice_proc = None                   # the line is over: nothing left to hush
            self.voice_preview = False
            if getattr(self, 'voice_queue', None) or getattr(self, '_voice_ready', None) or getattr(self, 'voice_streaming', False):
                self._voice_pump()                   # the next piece of the answer, if any
        if self.voice_proc and not self.voice_preview and (
                not b.bubble or b.bubble.get('id') != self.voice_id):
            self.hush()                              # the bubble went away: so does her voice
        # her line finished sounding: drop the streaming safety floor so the bubble
        # closes soon, but keep it alive while a segment is still being made or played
        if (b.bubble and not self.voice_preview
                and self.voice_id == b.bubble.get('id')
                and not b.chat_streaming and not b.bubble.get('stream')
                and not self.talking()
                and not getattr(self, 'voice_queue', None)
                and not getattr(self, '_voice_ready', None)
                and not getattr(self, 'voice_streaming', False)
                and getattr(self, '_voice_next', 1) == getattr(self, '_voice_seq', 0) + 1):
            b.bubble['until'] = min(b.bubble['until'], b.now + 2.0)
        self.ops = b.scene()
        self.web_tick()
        for w in self.windows:
            if w is self.active:
                w.area.queue_draw()
                w.update_input()
                w.set_keyboard(LayerShell.KeyboardMode.EXCLUSIVE if b.form and not self.form_picking else
                               LayerShell.KeyboardMode.ON_DEMAND if b.kb_hover else LayerShell.KeyboardMode.NONE)
            elif b.mode == 'held' or w.drew:
                w.area.queue_draw()                 # carried across / one last clear
        fps = b.fps()
        if self.painter.old is not None:
            fps = 60
        interval = int(1000 / fps)
        if self.timer is None or self.timer[1] != interval:
            if self.timer:
                GLib.source_remove(self.timer[0])
            self.timer = (GLib.timeout_add(interval, self.on_timer), interval)
        return False

    def on_timer(self):
        self.timer_fired = True
        old = self.timer
        self.frame()
        # frame() may have replaced the timer; keep this one only if it is still current
        return self.timer is old

    # ---- requests from the brain
    def handle_requests(self):
        b = self.brain
        while b.requests:
            r = b.requests.pop(0)
            kind = r[0]
            if kind == 'open':
                files.open_path(r[1])
            elif kind == 'job':
                self.queue.add(r[1], r[2], r[3])
            elif kind == 'pipette':
                system.pick_color(lambda hexc: self.ev(lambda b: b.pipette_done(hexc)),
                                  (self.effective_palette() or {}).get('accent'))
            elif kind == 'dnd':
                if not r[1]:
                    b.nap = False          # you asked her to wake: the nap guard must not ignore it
                self.world.set_dnd(r[1])
                b.set_sleep(r[1])          # the notification daemon will confirm; act now
            elif kind == 'copy':
                if not system.copy_text(r[1]):
                    Gdk.Display.get_default().get_clipboard().set(r[1])
            elif kind == 'run':
                system.spawn('sh', '-c', r[1])
            elif kind == 'url':
                system.open_url(r[1])
            elif kind == 'monitor_next':
                self.cycle_monitor()
            elif kind == 'outfit':
                self.apply_outfit()
            elif kind == 'sound':
                self.play_timer_sound()
            elif kind == 'sound_stop':
                self.stop_timer_sound()
            elif kind == 'voice':
                self.duck_end()     # her answer sounds at your loudness, not the ducked one
                self.speak(r[1], r[2], r[3], r[4] if len(r) > 4 else 0.5, r[5] if len(r) > 5 else '')
            elif kind == 'voice_stream':
                self.duck_end()
                self.speak_stream(r[1], r[2], r[3] if len(r) > 3 else 0.5)
            elif kind == 'voice_stream_blip':
                self.duck_end()
                self.speak_stream_blip(r[1], r[2], r[3], r[4] if len(r) > 4 else 0.5)
            elif kind == 'voice_stream_done':
                if hasattr(self, 'voice_stream_done'):
                    self.voice_stream_done(r[1])
            elif kind == 'chat_reply_done':
                self._chat_reply_remember(r[1])
            elif kind == 'listen':
                self.listen_ears(r[1])
            elif kind == 'listen_stop':
                threading.Thread(target=ears.stop, daemon=True).start()
            elif kind == 'wake_set':
                self.wake_set(r[1])
            elif kind == 'tap_set':
                self.tap_set(r[1])
            elif kind == 'chat_ask':
                self.chat_ask(r[1])
            elif kind == 'weather_ask':
                self.weather_ask(r[1])
            elif kind == 'hourly_ask':
                self.hourly_ask(r[1])
            elif kind == 'joke_ask':
                self.joke_ask(r[1])
            elif kind == 'note_voice':
                self.note_voice(r[1])
            elif kind == 'chat_models':
                self.fetch_chat_models()
            elif kind == 'mics':
                self.fetch_mics()
            elif kind == 'music_control':
                cmd = r[1]
                if cmd.startswith('volume'):
                    self.duck_keep()    # the level you asked for is the one that stays
                try:
                    music.music_control(cmd)
                except Exception:
                    pass
            elif kind == 'music_who':
                self.music_who()
            elif kind == 'music_search':
                self.music_search(r[1])
            elif kind == 'stop_all':
                self.hush()                    # her voice dies mid-word
                self.stop_timer_sound()
                try:
                    music.music_control('stop')
                except Exception:
                    pass
            elif kind == 'pick_sound':
                self.pick_sound()
            elif kind == 'form_pick':
                self.form_pick()
            elif kind == 'save_actions':
                config.save_actions(b.custom)
            elif kind == 'save':
                self.state.save()
            elif kind == 'save_settings':
                self.settings.save()
                tts.sync_profile(self.settings)       # a voice change relaunches the daemon
                music.apply_settings(self.settings)   # the server mpd may have moved
                self.web_sync_soon()                  # on / off / another port, live
            elif kind == 'web_open':
                self.web_open()
            elif kind == 'web_copy':
                self.web_copy()
            elif kind == 'web_rotate':
                self.web_rotate()
            elif kind == 'memory_clear_sassy':
                # «Дерзкая 18+» went off: its talks and jokes leave the diary,
                # vectors and all (yumi/memory.py, tag «sassy…»)
                threading.Thread(target=memory.clear_tag,
                                 args=(memory.TAG_SASSY,), daemon=True).start()
            elif kind == 'recolor':
                self.apply_palette(self.effective_palette())
            elif kind == 'quit':
                self.state.save()
                self.close()
                self.quit()
            elif kind == 'restart':
                self.state.save()
                self.close()
                self.restart()

    def restart(self):
        """start a fresh Yumi a moment from now (the app id is single-instance, so after we are gone)"""
        here = Path(__file__).resolve().parents[2]      # the app directory, in the repo or in share/yumi
        exe = next((str(p) for p in (here / 'bin' / 'yumi', here.parent.parent / 'bin' / 'yumi')
                    if p.exists()), shutil.which('yumi'))
        if exe:
            subprocess.Popen(['sh', '-c', 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; exec "$0"',
                              exe, str(os.getpid())], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.close()
        self.quit()

    # ---- timer sound: her 8-bit chime, or a file chosen in the settings
    def play_timer_sound(self):
        self.stop_timer_sound()
        own = self.settings.get('timer_sound')
        path = own if own and Path(own).exists() else config.SOUNDS / 'timer.wav'
        self.sound_procs = [system.play_file(path)]
        # twice, so it is hard to miss: the second one starts when the first has ended
        gap = 2.0
        try:
            import wave
            with wave.open(str(path)) as w:
                gap = w.getnframes() / w.getframerate() + 0.35
        except Exception:
            pass

        def again():
            self.sound_src = None
            self.sound_procs.append(system.play_file(path))
            return False
        self.sound_src = GLib.timeout_add(int(min(gap, 8.0) * 1000), again)

    def stop_timer_sound(self):
        """Stop pressed: no second chime, and the one playing is cut off"""
        src = getattr(self, 'sound_src', None)
        if src:
            GLib.source_remove(src)
            self.sound_src = None
        for p in getattr(self, 'sound_procs', []):
            try:
                if p:
                    p.terminate()
            except (OSError, AttributeError):
                pass
        self.sound_procs = []

    def pick_sound(self):
        dialog = Gtk.FileDialog()
        dialog.set_title(config.tr(self.settings, 'timer_sound'))
        flt = Gtk.FileFilter()
        flt.set_name(config.tr(self.settings, 'audio_files'))
        flt.add_mime_type('audio/*')
        for ext in ('wav', 'ogg', 'oga', 'mp3', 'flac', 'opus'):
            flt.add_suffix(ext)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dialog.set_filters(store)
        dialog.set_default_filter(flt)

        def done(dlg, res):
            try:
                f = dlg.open_finish(res)
            except GLib.Error:
                return                                   # cancelled
            if f and f.get_path():
                self.settings['timer_sound'] = f.get_path()
                self.settings.save()
                self.play_timer_sound()
                self.ev(lambda b: b.open_menu('set_sound', None) if b.menu else None)
        dialog.open(None, None, done)

    # ---- a picture for a button: the system's file dialog, like the timer sound
    def form_pick(self):
        self.form_picking = True               # the dialog needs the keyboard she holds
        flt = Gtk.FileFilter()
        flt.set_name(config.tr(self.settings, 'images'))
        flt.add_pixbuf_formats()
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)

        def done(dlg, res):
            self.form_picking = False
            try:
                path = dlg.open_finish(res).get_path()
                GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 32, 32, True)
            except GLib.Error as e:            # cancelled, or not a picture she can read
                bad = not e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
                self.ev(lambda b: b.set_face('worried', 1.5) if bad else None)
                return
            except TypeError:                  # not a local file
                self.ev(lambda b: b.set_face('worried', 1.5))
                return
            icon = config.keep_icon(path)
            self.ev(lambda b: b.form.update(icon=icon) if b.form else None)
        Gtk.FileDialog(title=config.tr(self.settings, 'ring_icon'), filters=store, default_filter=flt).open(None, None, done)

    def form_paste(self):
        def done(cb, res):
            try:
                text = cb.read_text_finish(res)
            except GLib.Error:
                return
            if text:
                self.ev(lambda b: b.form_text(text) if b.form else None)
        Gdk.Display.get_default().get_clipboard().read_text_async(None, done)

    # ---- jobs
    def job_label(self):
        return config.tr(self.settings, 'working')

    def job_start(self, job):
        self.ev(lambda b: b.job_progress(0.0, self.job_label()))

    def job_progress(self, job):
        self.ev(lambda b: b.job_progress(job.fraction, self.job_label()))

    def job_done(self, job, ok, msg):
        saved = None
        out = job.out
        if ok and out and out.exists():
            after = out.stat().st_size
            if job.op == 'compress':
                saved = jobs.human_saved(job.size_before, after)
                if not saved:                      # bigger or equal: not worth keeping
                    try:
                        out.unlink()
                    except OSError:
                        pass
                    out = None
            if out:
                self.recent.add(out)
        elif ok and msg == 'nogain':
            saved = None
        last = not self.queue.jobs
        if not ok or last:
            self.ev(lambda b: b.job_done(ok, job.op, out=out, saved=saved, fmt=job.arg, msg=msg))
        if not last:
            self.ev(lambda b: b.job_progress(0.0, self.job_label()))

    # ---- colors: the wallpaper's, or one picked by hand in the settings
    def effective_palette(self):
        if self.settings.get('recolor', True):
            if self.world.wall_palette:
                return self.world.wall_palette
        return palette_from_hue(self.settings.get('hue'))

    def on_palette(self, pal):
        if self.settings.get('recolor', True):
            self.apply_palette(pal)

    def on_caps(self, initial=False):
        """what this desktop lets her do; the settings show the rest as not available yet"""
        off = set()
        if self.world.wall is None:
            off.add('recolor')
        if not self.world.can_idle:
            off.add('idle_nap')
        changed = off != self.brain.unavailable
        self.brain.unavailable = off
        if changed and not initial:
            self.apply_palette(self.effective_palette())
            self.kick()

    def apply_palette(self, pal, meta=None):
        """reload every sprite (recolored to `pal`) in the background; with `meta` (another
        outfit) she changes into it only once its sprites are ready, so nothing jumps"""
        self.palette = pal
        delta = palette_delta(pal)
        meta = meta or self.meta
        self.load_gen += 1
        gen = self.load_gen

        def done(surfaces):
            if gen != self.load_gen:          # a newer load is on its way
                return False
            old_meta = self.meta
            if meta is not self.meta:
                self.meta = meta
                self.brain.meta = meta
                self.painter.set_meta(meta)
            self.painter.set_palette(pal)
            self.painter.swap(surfaces, time.monotonic(), old_meta)
            self.kick()
            return False

        def work():
            surfaces = load_surfaces(meta, delta)
            GLib.idle_add(lambda: done(surfaces))
        threading.Thread(target=work, daemon=True).start()
