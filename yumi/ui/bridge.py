"""The bridge the HTTP panel sees: GTK calls hop to the main thread, pure ones stay put."""
import threading
import time
from pathlib import Path

from yumi import chat, config, ears, files, jobs, memory, music, system


class WebBridge:
    """The bridge the HTTP server sees. Every call that touches GTK is wrapped by
    ``App._web_call``; the pure ones (chat, music, memory) stay on the HTTP thread."""

    def __init__(self, app):
        self.app = app

    def _on(self, fn, timeout=30):
        return self.app._web_call(fn, timeout)

    # ---- read side
    def snapshot(self):
        return self._on(self.app._web_snapshot)

    def configs(self):
        return self._on(self.app._web_configs)

    def config_get(self, name):
        return self._on(lambda: self.app._web_config_get(name))

    def config_save(self, name, value):
        return self._on(lambda: self.app._web_config_save(name, value), 60)

    def actions_get(self):
        return self._on(self.app._web_actions_get)

    def actions_save(self, actions):
        return self._on(lambda: self.app._web_config_save('actions', actions), 60)

    def action_run(self, index):
        b = self.app.brain
        self._on(lambda: b.extras_do('custom', int(index)))
        return {'ran': int(index)}

    def apply_settings(self, patch):
        return self._on(lambda: self.app._web_apply_settings(patch), 60)

    # ---- world actions
    def state_action(self, name, payload):
        a = self.app
        b = a.brain

        def run():
            if name in ('toggle', 'show'):
                b.toggle()
            elif name == 'hide':
                b.close_all()
                b.pop(False)
            elif name == 'home':
                b.go_home()
            elif name == 'monitor':
                a.cycle_monitor()
            elif name == 'pipette':
                b.start_pipette()
            elif name == 'recent':
                b.toggle_pile('recent')
            elif name == 'shelf':
                b.toggle_pile('shelf')
            elif name == 'dnd':
                on = bool(payload.get('on', not b.sleeping))
                if not on:
                    b.nap = False
                b.requests.append(('dnd', on))
            elif name == 'pin':
                on = bool(payload.get('on', not a.settings.get('pinned')))
                a.settings['pinned'] = on
                a.settings.save()
                if on:
                    b.pop(True)
            elif name == 'sound':
                b.requests.append(('sound', 'timer'))
            elif name == 'stop':
                b.stop_all()
            elif name == 'listen':
                b.requests.append(('listen', int(payload.get('seconds') or 8)))
            elif name == 'joke':
                b.requests.append(('joke_ask', {'lang': a.settings.get('speech_lang', 'en'),
                                                'text': payload.get('text') or ''}))
            elif name == 'tour':
                b.start_tour()
            elif name == 'config-dir':
                system.spawn('xdg-open', str(config.CONFIG_DIR))
            elif name == 'restart':
                b.requests.append(('restart',))
            elif name == 'quit':
                b.requests.append(('quit',))
            else:
                raise ValueError(f'unknown action: {name}')
            return {'action': name}
        return self._on(run)

    def files_recent(self):
        return self._on(self._files_recent)

    def _files_recent(self):
        items = sorted(getattr(self.app.recent, 'items', {}).items(), key=lambda x: -x[1])[:60]
        return [self._file_info(p, t) for p, t in items]

    @staticmethod
    def _file_info(path, mtime=None):
        p = Path(path)
        try:
            st = p.stat()
            size, mtime, exists = st.st_size, mtime or st.st_mtime, True
        except OSError:
            size, exists = 0, False
        kind = jobs.kind_of(p) or ('archive' if p.suffix.lower().lstrip('.') in
                                   ('zip', 'tar', 'gz', 'xz', 'zst', '7z', 'rar', 'bz2') else 'other')
        return {'path': str(p), 'name': p.name, 'size': size, 'mtime': mtime,
                'exists': exists, 'kind': kind, 'ext': p.suffix.lower().lstrip('.')}

    def files_shelf(self):
        return self._on(lambda: [self._file_info(p) for p in
                                 (self.app.state.get('shelf') or [])])

    def shelf_change(self, paths, add=True):
        def run():
            shelf = self.app.state.setdefault('shelf', [])
            for path in paths:
                p = str(Path(path).expanduser())
                if add:
                    if p not in shelf:
                        shelf.insert(0, p)
                else:
                    while p in shelf:
                        shelf.remove(p)
            del shelf[40:]
            self.app.state.save()
            return {'shelf': list(shelf)}
        return self._on(run)

    def files_open(self, path):
        def run():
            if not self.app._web_file_allowed(path):
                raise ValueError('this file is not one the panel knows')
            return {'opened': bool(files.open_path(path))}
        return self._on(run)

    def file_allowed(self, path):
        return self._on(lambda: self.app._web_file_allowed(path))

    def job_add(self, op, path, arg):
        def run():
            if op not in ('convert', 'compress'):
                raise ValueError('unknown job')
            if not self.app._web_file_allowed(path):
                raise ValueError('this file is not one the panel knows')
            p = Path(path).expanduser()
            if not p.is_file():
                raise ValueError('the file is gone')
            kind = jobs.kind_of(p)
            if not kind:
                raise ValueError('this file is not a picture, sound or video')
            allowed = jobs.TARGETS.get(kind, []) if op == 'convert' else jobs.PRESETS.get(kind, [])
            if arg not in allowed:
                raise ValueError(f'unknown target: {arg}')
            self.app.queue.add(op, str(p), arg)
            return {'queued': True, 'kind': kind, 'op': op, 'arg': arg}
        return self._on(run)

    def jobs_cancel(self):
        return self._on(lambda: self.app.queue.cancel())

    def timer_get(self):
        return self._on(self.app._web_timer_get)

    def timer(self, action, seconds):
        return self._on(lambda: self.app._web_timer(action, seconds))

    def ask(self, text, speak=True):
        def run():
            b = self.app.brain
            if b.thinking:
                return {'queued': False, 'reason': 'she is thinking already'}
            b.wake_up(text)
            return {'queued': True}
        return self._on(run)

    def chat(self, text):
        return self.app._web_chat(text)

    def chat_history(self):
        with self.app.web_chat_lock:
            return list(self.app.web_chat)

    def chat_clear(self):
        with self.app.web_chat_lock:
            self.app.web_chat = []
            self.app.save_web_chat()
        return {'cleared': True}

    def memory_recent(self, limit=50):
        return memory.recent(limit, hide_sassy=not self.app.settings.get('sassy'))

    def memory_search(self, query, limit=50):
        return memory.recall(query, self.app.settings, k=min(50, limit))

    def memory_clear(self, tag):
        if tag == 'all':
            return {'deleted': memory.clear_all()}
        return {'deleted': memory.clear_tag(tag)}

    def music_status(self):
        playing = music.now_playing()
        return {'now_playing': playing, 'volume': music.read_volume(),
                'mpd': bool(music.get_mpd_status()),
                'source': self.app.settings.get('music_source')}

    def music_search(self, kind, query):
        from yumi import library
        res = library.resolve(self.app.settings, kind, query)
        tracks = [{'title': t.get('title', ''), 'artist': t.get('artist', ''),
                   'album': t.get('album', ''), 'source': t.get('source', ''),
                   'path': t.get('path', ''), 'url': t.get('url', '')}
                  for t in (res.get('tracks') or [])[:40]]
        return {'source': res.get('source'), 'tracks': tracks, 'kind': kind, 'query': query}

    def music_play(self, kind, query):
        music.apply_settings(self.app.settings)
        report = music.play_query(kind, query, self.app.settings)
        track = report.get('track')
        return {'report': {'ok': report.get('ok'), 'source': report.get('source'),
                           'count': report.get('count'), 'kind': report.get('kind'),
                           'query': report.get('query'),
                           'track': {'artist': track[0], 'title': track[1]} if track else None}}

    def music_control(self, cmd):
        ok = music.music_control(cmd)
        return {'ok': bool(ok), 'cmd': cmd, 'volume': music.read_volume()}

    def voice_listen(self, seconds):
        res = ears.listen(lang=self.app.settings.get('speech_lang', 'en'),
                          secs=max(2, min(30, float(seconds or 8))),
                          size=self.app.settings.get('stt_size', 'small'),
                          mic=self.app.settings.get('stt_mic'),
                          engine=self.app.settings.get('stt_engine'))
        if not res:
            return {'heard': None, 'readings': None}
        heard = str(res.get('text') or res.get('vosk') or '').strip()
        return {'heard': heard or None, 'readings': res}

    def voice_stop(self):
        threading.Thread(target=ears.stop, daemon=True).start()
        return {'stopped': True}

    def voice_speak(self, text):
        self._on(lambda: self.app.brain.say_text(text))
        return {'spoke': True}

    def tools_time(self):
        return {'time': chat.the_time(self.app.settings.get('speech_lang', 'en')),
                'stamp': time.strftime('%Y-%m-%d %H:%M:%S')}

    def tools_weather(self, where, when):
        s = self.app.settings
        lang = s.get('speech_lang', 'en')
        place = where or str(s.get('weather_city') or '')
        return {'report': chat.weather_report(place, lang, when or 'today'),
                'place': place, 'when': when or 'today'}

    def tools_joke(self):
        s = self.app.settings
        return {'joke': chat.joke(s, s.get('speech_lang', 'en'))}

    def tools_params(self):
        lang = self.app.settings.get('speech_lang', 'en')
        return {'report': self._on(lambda: self.app.brain.params_report(lang))}

    def logs(self, name, lines):
        return self.app._web_logs(name, lines)
