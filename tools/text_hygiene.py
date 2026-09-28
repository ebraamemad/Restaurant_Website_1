"""فحص النصوص عشان محارف من لغات مش متوقّعة.

المستند بالعربي، وأنا بحكم عربي فبيحصل إني أكتب سطر عربي ويطلع فيه
حرف صيني أو ياباني أو روسي من غير ما أحس. الـglitches دي بتفضل
عايشة في الكود من غير حد يدري.

الطريقة هنا blacklist مش allowlist: بندوّر على نطاقات يونيكود بتاعة
لغات المفروض ما تظهرش (صيني، ياباني، كوري، روسي، يوناني، أرمني)،
وكمان على محرف التبديل اللي بيقول إن الملف اتقرأ وحش.

بكده مفيش false positive على المسافات والشرطات وعلامة الترقيم، ومفيش
استnegie على أسلوب الكتابة — الفحص على المحرف نفسه بس.

الاستخدام:
    python -m tools.text_hygiene           # يطبع السطور المشكلة
    python -m tools.text_hygiene --strict  # exit 1 لو لقى حاجة
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import unicodedata
from typing import Iterable, Iterator, List, Tuple

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".kilo",
    "static",
    "uploads",
}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".html",
    ".js",
    ".css",
    ".yml",
    ".yaml",
    ".ini",
    ".txt",
    ".example",
    ".cfg",
    ".toml",
}

SPECIAL_FILENAMES = {".env.example", "Dockerfile", ".dockerignore"}

# نطاقات يونيكود بتاع لغات مفروض ما تظهرش في نصوص المشروع.
FORBIDDEN_BLOCKS: List[Tuple[str, int, int]] = [
    ("CJK", 0x2E80, 0x2EFF),
    ("CJK", 0x3000, 0x303F),
    ("CJK", 0x3040, 0x309F),  # هيراغانا
    ("CJK", 0x30A0, 0x30FF),  # كاتاكانا
    ("CJK", 0x3400, 0x4DBF),
    ("CJK", 0x4E00, 0x9FFF),
    ("CJK", 0xF900, 0xFAFF),
    ("CJK", 0xFF00, 0xFFEF),  # fullwidth forms
    ("HANGUL", 0x1100, 0x11FF),
    ("HANGUL", 0x3130, 0x318F),
    ("HANGUL", 0xAC00, 0xD7AF),
    ("CYRILLIC", 0x0400, 0x052F),
    ("GREEK", 0x0370, 0x03FF),
    ("ARMENIAN", 0x0530, 0x058F),
    ("HEBREW", 0x0590, 0x05FF),
    ("THAI", 0x0E00, 0x0E7F),
    ("DEVANAGARI", 0x0900, 0x097F),
    ("GEORGIAN", 0x10A0, 0x10FF),
    ("TIFINAGH", 0x2D30, 0x2D7F),
    ("ETHIOPIC", 0x1200, 0x137F),
    ("CHEROKEE", 0x13A0, 0x13FF),
    ("RUNIC", 0x16A0, 0x16FF),
    ("TAGALOG", 0x1700, 0x171F),
    ("KHMER", 0x1780, 0x17FF),
    ("MONGOLIAN", 0x1800, 0x18AF),
    ("BOPOMOFO", 0x3100, 0x312F),
    ("YI", 0xA000, 0xA48F),
    ("VAI", 0xA500, 0xA63F),
    ("CYRILLIC_EXT", 0x1C80, 0x1C8F),
    ("EMOTICON", 0x1F600, 0x1F64F),
    ("SUPPLEMENTAL", 0x1F900, 0x1F9FF),
    ("PRIVATE_USE", 0xE000, 0xF8FF),
    ("VARIATION_SELECTOR", 0xFE00, 0xFE0F),
]

# محرف التبديل: بيقول إن الملف اتقرأ بترميز غلط.
REPLACEMENT_CHAR = "\ufffd"

# سطور مسموح بيها نصايح الفحص نفسه، عشان متبلّغش عن الكود بتاعه.
SELF_EXEMPT = "tools/text_hygiene.py"

def _block_name(char: str) -> str | None:
    code = ord(char)
    for name, low, high in FORBIDDEN_BLOCKS:
        if low <= code <= high:
            return name
    if char == REPLACEMENT_CHAR:
        return "REPLACEMENT"
    return None


def scan_text(text: str) -> Iterator[Tuple[int, str, str, str]]:
    """يدي (رقم السطر, المحرف, نوع المشكلة, السطر)."""
    for line_number, line in enumerate(text.splitlines(), start=1):
        reported: set[str] = set()
        for char in line:
            block = _block_name(char)
            if block is None or block in reported:
                continue
            reported.add(block)
            snippet = line.strip()
            if len(snippet) > 100:
                snippet = snippet[:100] + "..."
            yield line_number, char, block, snippet


def iter_text_files(root: pathlib.Path) -> Iterator[pathlib.Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in SPECIAL_FILENAMES:
            yield path


def scan_repo(root: pathlib.Path) -> List[str]:
    problems: List[str] = []
    for path in iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{path}: الملف مش UTF-8 صالح ({exc.reason})")
            continue
        except OSError as exc:
            problems.append(f"{path}: مقدرناش نقراه ({exc.strerror})")
            continue
        for line_number, char, block, snippet in scan_text(text):
            name = unicodedata.name(char, "?")
            problems.append(
                f"{path}:{line_number}: [{block}] {char!r} {name} — {snippet}"
            )
    return problems


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true", help="exit 1 لو لقى محارف مش متوقّعة"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = pathlib.Path(__file__).resolve().parents[1]
    problems = scan_repo(root)
    if not problems:
        print("النصوص نضيفة: مفيش محارف من لغات مش متوقّعة.")
        return 0
    print(f"لقيت {len(problems)} مشكلة:\n")
    for problem in problems:
        print(f"  {problem}")
    if not args.strict:
        print("\n(استخدم --strict لو عايز الكود يفشل على ده)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
