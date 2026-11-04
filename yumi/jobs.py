"""Convert and compress media: command building (pure) and a small job queue (Gio).

Never overwrites anything: results go next to the source as `name.ext`, `name (1).ext`...
Heavy work runs under `nice -n 15`; ffmpeg reports progress through `-progress pipe:1`.
"""
import os
import shutil
from pathlib import Path

IMAGE = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff', 'ico', 'avif', 'heic',
         'heif', 'svg', 'jxl'}
VIDEO = {'mp4', 'mkv', 'webm', 'mov', 'avi', 'm4v', 'flv', 'wmv', '3gp', 'mpg', 'mpeg', 'ts'}
AUDIO = {'mp3', 'ogg', 'oga', 'opus', 'flac', 'wav', 'm4a', 'aac', 'wma', 'aiff'}
NEEDS_MAGICK = {'svg', 'heic', 'heif', 'avif', 'jxl', 'tif', 'tiff', 'ico'}

TARGETS = {
    'image': ['png', 'jpg', 'webp', 'gif', 'ico'],
    'video': ['mp4', 'webm', 'mkv', 'gif', 'mp3', 'opus'],
    'audio': ['mp3', 'ogg', 'opus', 'flac', 'wav', 'm4a'],
}
PRESETS = {
    'image': ['p_small', 'p_1mb', 'p_half'],
    'video': ['p_10', 'p_25', 'p_50', 'p_720', 'p_480'],
    'audio': ['p_opus', 'p_mp3'],
}
SIZE_MB = {'p_10': 10, 'p_25': 25, 'p_50': 50}
NICE = ['nice', '-n', '15']
FF = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']


def ext_of(path):
    return Path(path).suffix.lower().lstrip('.')


def kind_of(path):
    e = ext_of(path)
    return 'image' if e in IMAGE else 'video' if e in VIDEO else 'audio' if e in AUDIO else None


def have(tool):
    return shutil.which(tool) is not None


def unique(path):
    """`path` itself if free, otherwise `stem (1).ext`, `stem (2).ext`, ..."""
    p = Path(path)
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    n = 1
    while True:
        q = p.with_name(f'{stem} ({n}){suf}')
        if not q.exists():
            return q
        n += 1


def out_dir(src):
    d = Path(src).parent
    return d if os.access(d, os.W_OK) else Path.home()


def targets_for(path):
    k = kind_of(path)
    e = ext_of(path)
    e = 'jpg' if e == 'jpeg' else e
    return [t for t in TARGETS.get(k, []) if t != e]


def presets_for(path):
    return list(PRESETS.get(kind_of(path), []))


class Step:
    """one command; `progress` means ffmpeg writes -progress lines we can turn into a percent"""

    def __init__(self, argv, progress=False, tool=None):
        self.argv = argv
        self.progress = progress
        if tool is None:
            tool = argv[len(NICE)] if argv[:len(NICE)] == NICE else argv[0]
        self.tool = tool

    def __repr__(self):
        return f'Step({self.argv!r})'


def _ff(src, out, *args):
    return Step(NICE + FF + ['-i', str(src), *args, '-progress', 'pipe:1', '-nostats', str(out)],
                progress=True, tool='ffmpeg')


def magick_cmd():
    """ImageMagick 7 is `magick`; distros still on 6 only have `convert`"""
    return 'magick' if have('magick') or not have('convert') else 'convert'


def _magick(*args):
    tool = magick_cmd()
    return Step(NICE + [tool, *map(str, args)], tool=tool)


AUDIO_CODEC = {
    'mp3': ['-c:a', 'libmp3lame', '-q:a', '2'],
    'ogg': ['-c:a', 'libvorbis', '-q:a', '5'],
    'opus': ['-c:a', 'libopus', '-b:a', '128k'],
    'flac': ['-c:a', 'flac'],
    'wav': ['-c:a', 'pcm_s16le'],
    'm4a': ['-c:a', 'aac', '-b:a', '192k'],
}
GIF_VF = ("fps=12,scale='min(480,iw)':-2:flags=lanczos,split[a][b];"
          "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")


def plan_convert(src, target):
    """(steps, output path) to turn `src` into format `target`"""
    src = Path(src)
    k, e = kind_of(src), ext_of(src)
    out = unique(out_dir(src) / f'{src.stem}.{target}')
    if k == 'image':
        if have('magick') or have('convert') or e in NEEDS_MAGICK or target == 'ico':
            args = []
            if e == 'svg':
                args += ['-density', '192', '-background', 'none']
            args.append(f'{src}[0]' if target in ('png', 'jpg', 'ico') and e == 'gif' else str(src))
            if target == 'jpg':
                args += ['-background', 'white', '-flatten', '-quality', '92']
            if target == 'ico':
                args += ['-resize', '256x256>', '-define', 'icon:auto-resize=256,128,64,48,32,16']
            return [_magick(*args, out)], out
        extra = ['-frames:v', '1'] if target in ('png', 'jpg', 'ico') else []
        if target == 'gif':
            extra = ['-vf', GIF_VF, '-loop', '0']
        return [_ff(src, out, *extra)], out
    if k in ('video', 'audio'):
        if target in AUDIO_CODEC:
            return [_ff(src, out, '-vn', *AUDIO_CODEC[target])], out
        if target == 'mp4':
            return [_ff(src, out, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                        '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k',
                        '-movflags', '+faststart')], out
        if target == 'webm':
            return [_ff(src, out, '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '33',
                        '-deadline', 'realtime', '-cpu-used', '8', '-row-mt', '1',
                        '-c:a', 'libopus', '-b:a', '128k')], out
        if target == 'mkv':
            return [_ff(src, out, '-map', '0', '-c', 'copy')], out
        if target == 'gif':
            return [_ff(src, out, '-vf', GIF_VF, '-loop', '0')], out
    raise ValueError(f'cannot convert {e} to {target}')


def video_bitrates(duration, mb):
    """(video kbps, audio kbps, max height) so the file ends up under `mb` megabytes"""
    total = mb * 8 * 1024 * 0.93 / max(duration, 1.0)          # kbit/s, 7% for the container
    audio = 96 if total > 600 else 64 if total > 200 else 32
    video = max(total - audio, 40)
    height = 1080 if video > 2500 else 720 if video > 1000 else 480 if video > 350 else 360
    return int(video), audio, height


def plan_compress(src, preset, duration=None):
    src = Path(src)
    k, e = kind_of(src), ext_of(src)
    d = out_dir(src)
    if k == 'image':
        if preset == 'p_1mb':
            out = unique(d / f'{src.stem}-1mb.jpg')
            return [_magick(f'{src}[0]', '-strip', '-background', 'white', '-flatten',
                            '-define', 'jpeg:extent=1MB', out)], out
        if preset == 'p_half':
            out = unique(d / f'{src.stem}-half.{e}')
            return [_magick(src, '-resize', '50%', out)], out
        out = unique(d / f'{src.stem}-small.{e}')
        if e == 'png':
            steps = []
            if have('pngquant'):
                steps.append(Step(NICE + ['pngquant', '--quality=60-85', '--speed', '3',
                                          '--strip', '--output', str(out), str(src)]))
                if have('oxipng'):
                    steps.append(Step(NICE + ['oxipng', '-o', '2', '--strip', 'safe', str(out)]))
            else:
                steps.append(Step(NICE + ['oxipng', '-o', '3', '--strip', 'safe', '--out',
                                          str(out), str(src)]))
            return steps, out
        if e in ('jpg', 'jpeg') and have('jpegoptim') and not (have('magick') or have('convert')):
            return [Step(['cp', str(src), str(out)]),
                    Step(NICE + ['jpegoptim', '--strip-all', '-m80', str(out)])], out
        if e == 'gif':
            return [_magick(src, '-layers', 'Optimize', out)], out
        return [_magick(src, '-strip', '-quality', '78' if e == 'webp' else '80', out)], out
    if k == 'video':
        if preset in SIZE_MB:
            mb = SIZE_MB[preset]
            v, a, h = video_bitrates(duration or 60.0, mb)
            out = unique(d / f'{src.stem}-{mb}mb.mp4')
            return [_ff(src, out, '-vf', f"scale=-2:'min({h},ih)'", '-c:v', 'libx264',
                        '-preset', 'veryfast', '-b:v', f'{v}k', '-maxrate', f'{int(v * 1.2)}k',
                        '-bufsize', f'{v * 2}k', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                        '-b:a', f'{a}k', '-movflags', '+faststart')], out
        h = 720 if preset == 'p_720' else 480
        out = unique(d / f'{src.stem}-{h}p.mp4')
        return [_ff(src, out, '-vf', f"scale=-2:'min({h},ih)'", '-c:v', 'libx264',
                    '-preset', 'veryfast', '-crf', '26', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart')], out
    if k == 'audio':
        if preset == 'p_mp3':
            out = unique(d / f'{src.stem}-128k.mp3')
            return [_ff(src, out, '-vn', '-c:a', 'libmp3lame', '-b:a', '128k')], out
        out = unique(d / f'{src.stem}-96k.opus')
        return [_ff(src, out, '-vn', '-c:a', 'libopus', '-b:a', '96k')], out
    raise ValueError(f'cannot compress {e}')


def missing_tools(steps):
    return sorted({s.tool for s in steps if s.tool not in ('cp',) and not have(s.tool)})


def human_saved(before, after):
    """'73%' style saving, or None when the file did not shrink"""
    if not before or after is None or after >= before:
        return None
    return f'{round((1 - after / before) * 100)}%'


# ---------------------------------------------------------------------------------------------
# the queue (needs GLib/Gio; imported lazily so the planners stay testable without them)

class Job:
    def __init__(self, op, src, arg):
        self.op = op            # 'convert' / 'compress'
        self.src = Path(src)
        self.arg = arg          # target format or preset key
        self.steps = []
        self.out = None
        self.fraction = 0.0
        self.duration = None
        self.size_before = None


class Queue:
    """runs jobs one by one; callbacks: progress(job), done(job, ok, message)"""

    def __init__(self, on_progress, on_done, on_start=None):
        from gi.repository import Gio, GLib  # noqa: F401
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_start = on_start
        self.jobs = []
        self.current = None
        self.proc = None

    @property
    def busy(self):
        return self.current is not None or bool(self.jobs)

    def add(self, op, src, arg):
        self.jobs.append(Job(op, src, arg))
        if self.current is None:
            self._next()

    def cancel(self):
        self.jobs.clear()
        if self.proc:
            self.proc.force_exit()

    def _probe(self, job):
        from gi.repository import Gio
        if job.src and kind_of(job.src) in ('video', 'audio') and have('ffprobe'):
            try:
                p = Gio.Subprocess.new(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                        '-of', 'csv=p=0', str(job.src)],
                                       Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
                ok, out, _ = p.communicate_utf8(None, None)
                job.duration = float((out or '').strip() or 0) or None
            except Exception:
                job.duration = None

    def _next(self):
        from gi.repository import GLib
        if not self.jobs:
            self.current = None
            return
        job = self.current = self.jobs.pop(0)
        try:
            job.size_before = job.src.stat().st_size
            if job.op == 'compress' and job.arg in SIZE_MB and \
                    job.size_before <= SIZE_MB[job.arg] * 1024 * 1024:
                GLib.idle_add(self._finish, job, True, 'nogain')     # already small enough
                return
            self._probe(job)
            if job.op == 'convert':
                job.steps, job.out = plan_convert(job.src, job.arg)
            else:
                job.steps, job.out = plan_compress(job.src, job.arg, job.duration)
        except (OSError, ValueError) as e:
            GLib.idle_add(self._finish, job, False, str(e))
            return
        miss = missing_tools(job.steps)
        if miss:
            GLib.idle_add(self._finish, job, False, 'missing:' + ','.join(miss))
            return
        if self.on_start:
            self.on_start(job)
        self._run(job, 0)

    def _run(self, job, i):
        from gi.repository import Gio, GLib
        if i >= len(job.steps):
            self._finish(job, True, '')
            return
        step = job.steps[i]
        try:
            self.proc = Gio.Subprocess.new(step.argv, Gio.SubprocessFlags.STDOUT_PIPE |
                                           Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as e:
            self._finish(job, False, e.message)
            return
        if step.progress:
            stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
            self._read(stream, job, i)
        self.proc.wait_async(None, self._exited, (job, i))

    def _read(self, stream, job, i):
        def got(s, res):
            try:
                line, _ = s.read_line_finish_utf8(res)
            except Exception:
                return
            if line is None:
                return
            if line.startswith('out_time_us=') and job.duration:
                try:
                    us = int(line.split('=', 1)[1])
                    part = min(max(us / 1e6 / job.duration, 0.0), 1.0)
                    job.fraction = (i + part) / len(job.steps)
                    self.on_progress(job)
                except ValueError:
                    pass
            s.read_line_async(0, None, got)
        stream.read_line_async(0, None, got)

    def _exited(self, proc, res, data):
        job, i = data
        try:
            proc.wait_finish(res)
        except Exception:
            pass
        self.proc = None
        ok = proc.get_if_exited() and proc.get_exit_status() == 0
        # pngquant exits 98/99 when it cannot reach the quality: treat as "no gain", not failure
        if not ok and job.steps[i].tool == 'pngquant' and proc.get_exit_status() in (98, 99):
            self._finish(job, True, 'nogain')
            return
        if not ok:
            try:
                if job.out and job.out.exists() and job.out.stat().st_size == 0:
                    job.out.unlink()
            except OSError:
                pass
            self._finish(job, False, f'{job.steps[i].tool} failed')
            return
        job.fraction = (i + 1) / len(job.steps)
        self.on_progress(job)
        self._run(job, i + 1)

    def _finish(self, job, ok, msg):
        self.on_done(job, ok, msg)
        self.current = None
        self._next()
        return False
