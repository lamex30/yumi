"""Which ears and which voice are actually here (no GTK, no venv imports, no HTTP).

The browser panel and the app's snapshot ask this module for a cheap answer:
does the openWakeWord model and its own Python exist, is the STT venv in place,
is the Nemotron model on disk, is the TTS venv ready?  Nothing here starts a
daemon or imports torch/vosk: only ``Path.exists`` and a JSON read, so it is
safe to call on every status tick.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config

HOME = Path.home() / '.local/share/yumi'
STT_HOME = Path(os.environ.get('YUMI_STT_HOME') or HOME / 'stt')
STT_PY = Path(os.environ.get('YUMI_STT_PY') or STT_HOME / 'bin/python')
OWW_PY = Path(os.environ.get('YUMI_OWW_PY') or HOME / 'oww/bin/python')
OWW_MODEL = Path(os.environ.get('YUMI_OWW_MODEL') or HOME / 'oww/models/yumi.onnx')
TTS_PY = Path(os.environ.get('YUMI_TTS_PY') or HOME / 'tts/bin/python')
NEMO_DIR = STT_HOME / ('sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11')
NEMO_MODEL = NEMO_DIR / 'encoder.int8.onnx'
TTS_CONF = Path(config.CONFIG_DIR) / 'tts.json'

RU_SPEAKERS = ['kseniya', 'baya', 'xenia', 'aidar', 'eugene']
EN_SPEAKERS = [f'en_{i}' for i in range(118)]
RU_MODELS = ['v5_6_ru', 'v4_ru']


def _size(path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def tts_profile():
    """the profile tts_daemon would load now (tts.json over the defaults)"""
    data = {name: config.TTS_PROFILE_DEFAULTS[key]
            for key, name in config.TTS_PROFILE_KEYS.items()}
    try:
        saved = json.loads(TTS_CONF.read_text())
        if isinstance(saved, dict):
            for name in data:
                if name in saved and saved[name] is not None:
                    data[name] = saved[name]
    except (OSError, ValueError):
        pass
    return data


def status(settings):
    """everything the panel needs to draw the three pickers and their badges"""
    profile = tts_profile()
    return {
        'wake_word': str(settings.get('wake_word') or 'юми'),
        'wake_ear': str(settings.get('wake_ear') or 'auto'),
        'stt_engine': str(settings.get('stt_engine') or 'whisper'),
        'activation': {
            'oww': {
                'available': bool(OWW_PY.exists() and OWW_MODEL.exists()),
                'python': str(OWW_PY),
                'python_ok': OWW_PY.exists(),
                'model': str(OWW_MODEL),
                'model_ok': OWW_MODEL.exists(),
                'model_bytes': _size(OWW_MODEL),
            },
            'vosk': {
                'available': STT_PY.exists(),
                'python': str(STT_PY),
            },
        },
        'judge': {
            'whisper': {
                'available': STT_PY.exists(),
                'python': str(STT_PY),
            },
            'nemotron': {
                'available': NEMO_MODEL.exists(),
                'model': str(NEMO_MODEL),
                'model_bytes': _size(NEMO_MODEL),
            },
        },
        'tts': {
            'available': TTS_PY.exists() or bool(os.environ.get('YUMI_SYS_TTS')),
            'python': str(TTS_PY),
            'python_ok': TTS_PY.exists(),
            'profile': profile,
            'speakers': {'ru': list(RU_SPEAKERS), 'en': list(EN_SPEAKERS)},
            'models': list(RU_MODELS),
        },
    }
