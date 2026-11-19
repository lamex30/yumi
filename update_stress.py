#!/usr/bin/env python3
"""Обновить ~/.config/yumi/stress.json списками kost-t-human (silero-stress промахи).

Скачивает два файла из gist kost-t-human:
- stress_fixes.txt — 1035 слов, где silero-stress 1.5 ставит неверное ударение
- hidden_homographs.txt — 240 омографов, где модель ставит одну форму, а словари дают другую

Результат пишется в ~/.config/yumi/stress.json — формат, который понимает
apply_stress в yumi/tts.py ({"слово": "с+ударное"}).
"""
import json
import re
from pathlib import Path
from urllib.request import urlopen

GIST_RAW = "https://gist.githubusercontent.com/kost-t-human/c13a6fcec71bf7ddaecb06842f6fded4/raw/"

# Стартовый набор из yumi/tts.py (чтобы не потерять при обновлении)
STARTER_STRESS = {
    'торты': 'т+орты', 'тортов': 'т+ортов', 'тортами': 'т+ортами',
    'звонит': 'звон+ит', 'позвонит': 'позвон+ит', 'позвонишь': 'позвон+ишь',
    'договор': 'догов+ор', 'договора': 'догов+ора',
    'каталог': 'катал+ог', 'квартал': 'кварт+ал', 'аппарат': 'аппар+ат',
}

STRESS_FILE = Path.home() / '.config' / 'yumi' / 'stress.json'


def fetch(name: str) -> str:
    with urlopen(GIST_RAW + name, timeout=30) as r:
        return r.read().decode('utf-8')


def parse_stress_fixes(text: str) -> dict[str, str]:
    """stress_fixes.txt: слово = ударение  # комментарий"""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ' = ' not in line:
            continue
        word, stressed = line.split(' = ', 1)
        word = word.strip().lower()
        stressed = stressed.split('#', 1)[0].strip()
        if word and '+' in stressed:
            result[word] = stressed
    return result


def parse_hidden_homographs(text: str) -> dict[str, str]:
    """
    hidden_homographs.txt: слово | частота | модель | варианты AOT/Викисловаря
    Берём вариант ударе́ния, который отличается от того, что ставит модель
    (потому что модель на этих омографах ошибается).
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 4:
            continue
        word = parts[0].strip().lower()
        model_stress = parts[2].strip()
        variants_raw = parts[3].strip()
        variants = [v.strip() for v in re.split(r'\s*/\s*', variants_raw) if v.strip()]
        chosen = None
        for v in variants:
            if v != model_stress:
                chosen = v
                break
        if word and chosen and '+' in chosen:
            result[word] = chosen
    return result


def main():
    print("Скачиваю списки kost-t-human...")
    fixes_text = fetch('stress_fixes.txt')
    homo_text = fetch('hidden_homographs.txt')

    fixes = parse_stress_fixes(fixes_text)
    homos = parse_hidden_homographs(homo_text)

    # загружаем текущий словарь пользователя, если есть
    current: dict[str, str] = {}
    if STRESS_FILE.exists():
        try:
            current = json.loads(STRESS_FILE.read_text(encoding='utf-8'))
            current = {k.lower(): v for k, v in current.items()
                       if isinstance(k, str) and isinstance(v, str) and '+' in v}
        except (OSError, ValueError):
            current = {}

    # приоритет: стартовый -> текущий пользователя -> kost-t-human fixes -> homographs
    merged: dict[str, str] = {}
    merged.update(STARTER_STRESS)
    merged.update(current)
    merged.update(fixes)
    merged.update(homos)

    STRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STRESS_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=4, sort_keys=True) + '\n',
        encoding='utf-8',
    )

    print(f"Готово. Всего слов в словаре: {len(merged)}")
    print(f"  стартовый набор yumi: {len(STARTER_STRESS)}")
    print(f"  из вашего старого stress.json: {len(current)}")
    print(f"  из stress_fixes.txt (kost-t-human): {len(fixes)}")
    print(f"  из hidden_homographs.txt (kost-t-human): {len(homos)}")
    print(f"Файл сохранён: {STRESS_FILE}")
    print()
    print("Юми подхватит новый stress.json автоматически при следующей фразе —")


if __name__ == '__main__':
    main()
