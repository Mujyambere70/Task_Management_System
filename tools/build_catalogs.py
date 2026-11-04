"""
tools/build_catalogs.py - builds translations/<lang>.json from the tables in tools/tr/*.tsv.

Each line of a table: English <TAB> Russian <TAB> French <TAB> Spanish <TAB> Arabic <TAB> Chinese <TAB> Kinyarwanda
"{0}", "{1}" are gaps for names and numbers. Run:  python tools/build_catalogs.py
"""

import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "translations")
LANGS = ["ru", "fr", "es", "ar", "zh", "rw"]


def main():
    catalogs = {lang: {} for lang in LANGS}
    for path in sorted(glob.glob(os.path.join(HERE, "tr", "*.tsv"))):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.rstrip("\n")
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) != 7:
                    raise SystemExit(f"{path}:{n}: expected 7 columns, found {len(parts)}")
                key = " ".join(parts[0].split())
                for lang, text in zip(LANGS, parts[1:]):
                    if text.strip():
                        catalogs[lang][key] = text.strip()
    os.makedirs(OUT, exist_ok=True)
    for lang, data in catalogs.items():
        with open(os.path.join(OUT, f"{lang}.json"), "w", encoding="utf-8") as f:
            json.dump(dict(sorted(data.items())), f, ensure_ascii=False, indent=1)
        print(lang, len(data), "texts")


if __name__ == "__main__":
    main()
