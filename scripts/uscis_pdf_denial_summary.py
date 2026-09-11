"""Извлечение текста из PDF и краткое описание причины отказа через OpenAI.

Общий процесс (конвейер):
  1) Находим файлы *.pdf в каталоге и подпапках тем/годов (по умолчанию files/uscis_pdfs/pdfs);
     при тестовом лимите берём только первые N по имени (см. MAX_PDF_FILES_FOR_TEST).
  2) Для каждого PDF последовательно извлекаем текст (не более первых 12 000 символов,
     считая переводы строк между страницами; дальнейшие страницы не читаются) — без OCR,
     только встроенный текст; отсканированные страницы без слоя текста дадут пустой результат.
  3) Классифицируем подтип I-140 EB-2: niw / eb2_standard / unclear (маркеры в тексте + поле
     category от модели).
  4) Извлечённый текст отправляем в Chat Completions API OpenAI: в JSON — category, сведения о
     бенефициаре (профессия, должность, сфера) и развёрнутое описание отказа (на русском).
  5) Для каждого файла одна строка TSV:
     «путь.pdf<TAB>category<TAB>профессия…<TAB>причина отказа…»
     → files/uscis_pdfs/summary_<topic>.txt (например summary_18.txt; UTF-8).
     При нескольких топиках в одном прогоне — отдельный файл на каждый код темы.
  6) По умолчанию копии PDF раскладываются в files/uscis_pdfs/classified/<category>/…

Переменные окружения:
  OPENAI_API_KEY — ключ API (обязательно)
  OPENAI_MODEL   — модель чата (по умолчанию gpt-4o-mini)

Файл .env в корне проекта (рядом с run.py) подхватывается автоматически при запуске скрипта.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pypdf import PdfReader

# Корень репозитория: scripts/ -> родитель = проект; так пути не зависят от текущей папки запуска.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Каталог по умолчанию совпадает с тем, куда uscis_pdf_download.py кладёт решения.
DEFAULT_PDF_DIR = PROJECT_ROOT / "files" / "uscis_pdfs" / "pdfs"
USCIS_PDFS_DIR = PROJECT_ROOT / "files" / "uscis_pdfs"
OUTPUT_FILENAME_PREFIX = "summary_"
CLASSIFIED_DIRNAME = "classified"
# Код темы AAO в пути pdfs/<topic>/<year>/… или имя каталога --dir.
_TOPIC_DIR_RE = re.compile(r"^(?:\d+|All)$", re.IGNORECASE)
_YEAR_DIR_RE = re.compile(r"^(?:19|20)\d{2}$")
# Допустимые метки подтипа EB-2 (колонка category в TSV и имена подпапок).
CATEGORY_NIW = "niw"
CATEGORY_EB2_STANDARD = "eb2_standard"
CATEGORY_UNCLEAR = "unclear"
VALID_CATEGORIES = frozenset(
    {CATEGORY_NIW, CATEGORY_EB2_STANDARD, CATEGORY_UNCLEAR}
)
# Максимум символов, извлекаемых из PDF (начало документа; перевод строки между страницами входит в лимит).
PDF_TEXT_MAX_CHARS = 12_000
# Для тестов: не более стольких PDF подряд (после sorted по имени). None — обработать все файлы в каталоге.
MAX_PDF_FILES_FOR_TEST: int | None = None

# Маркеры NIW в тексте AAO (регистр не важен).
_NIW_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"national\s+interest\s+waiver",
        r"\bniw\b",
        r"matter\s+of\s+dhanasar",
        r"\bdhanasar\b",
        r"waiv(?:e|er|ing)\s+of\s+the\s+(?:job\s+offer|labor\s+certification)",
        r"seeking\s+a\s+waiver\s+of\s+the\s+job\s+offer",
    )
)
# Признаки обычного EB-2 с PERM / job offer (без NIW).
_EB2_STANDARD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"labor\s+certification",
        r"permanent\s+labor\s+certification",
        r"\bPERM\b",
        r"advanced\s+degree\s+professional",
        r"member\s+of\s+the\s+professions\s+holding\s+an\s+advanced\s+degree",
        r"exceptional\s+ability",
        r"ability\s+to\s+pay",
    )
)

# Ответ только JSON (режим json_object у API); поля парсятся в analyze_case.
SYSTEM_PROMPT_JSON = (
    "Ты помощник по анализу административных решений USCIS/AAO. "
    "Отвечай только одним JSON-объектом; значения profession и denial — на русском. "
    "Поле category — одно из: niw, eb2_standard, unclear (латиницей). "
    "Поля profession и denial могут быть из нескольких предложений, если нужно для полноты."
)

# В {text} — фрагмент решения. Модель возвращает category + profession + denial.
USER_TEMPLATE_JSON = """Ниже фрагмент текста решения по делу (петиция/апелляция и т.д.).

Сделай:
1) category: классификация петиции I-140 / EB-2. Ровно одно значение:
   — "niw" — National Interest Waiver (national interest waiver, Matter of Dhanasar, отказ от job offer / labor certification в национальных интересах);
   — "eb2_standard" — обычный EB-2 (advanced degree или exceptional ability) с job offer и/или labor certification, без NIW;
   — "unclear" — по фрагменту нельзя уверенно отнести к niw или eb2_standard (другая категория, обрыв текста и т.п.).

2) profession: для бенефициара петиции укажи в одном связном тексте (пара предложений допустимо):
   — профессию / специальность (occupation);
   — в какой должности (job title) он работает или будет работать, если это сказано в тексте;
   — в какой сфере, отрасли или области деятельности (field / industry), если это есть в тексте.
   Если чего-то нет в отрывке — явно напиши, что не указано. Если бенефициар не идентифицируется — «не указано в тексте».

3) denial: опиши подробно на русском:
   — есть ли отказ в удовлетворении апелляции/ходатайства и в чём суть решения;
   — какие именно доказательства, доводы, документы или виды доказательств были отклонены, не приняты, не засчитаны или признаны недостаточными/неубедительными (перечисли по смыслу текста: например отчёты, письма, экспертные заключения, публикации, критерии EB-1/NIW и т.д., если они фигурируют);
   — если отказа нет или текст обрывается — так и укажи.

Текст документа:
---
{text}
---

Верни строго JSON вида:
{{"category": "niw|eb2_standard|unclear", "profession": "...", "denial": "..."}}
"""


def extract_pdf_text(path: Path) -> str:
    """Собирает текст с начала PDF в одну строку, не более PDF_TEXT_MAX_CHARS символов.

    Порядок страниц — как в файле. Между страницами вставляется перевод строки; он учитывается
    в лимите. Как только набрано достаточно символов, остальные страницы не обрабатываются.
    Пустые страницы пропускаются.
    """
    reader = PdfReader(str(path))
    parts: list[str] = []
    length = 0  # длина результата как len("\\n".join(parts))
    for page in reader.pages:
        t = page.extract_text()
        if not t:
            continue
        sep = 1 if parts else 0
        if length + sep + len(t) <= PDF_TEXT_MAX_CHARS:
            parts.append(t)
            length += sep + len(t)
            continue
        need = PDF_TEXT_MAX_CHARS - length - sep
        if need > 0:
            parts.append(t[:need])
        break
    return "\n".join(parts).strip()


def classify_eb2_subtype(document_text: str) -> str:
    """Эвристика по тексту: niw, если есть маркеры NIW; иначе eb2_standard при PERM/job-offer признаках; иначе unclear."""
    text = document_text.strip()
    if not text:
        return CATEGORY_UNCLEAR
    if any(p.search(text) for p in _NIW_PATTERNS):
        return CATEGORY_NIW
    if any(p.search(text) for p in _EB2_STANDARD_PATTERNS):
        return CATEGORY_EB2_STANDARD
    return CATEGORY_UNCLEAR


def merge_category(keyword_cat: str, model_cat: str) -> str:
    """Сводит эвристику и ответ модели. NIW по маркерам важнее; иначе берём валидный ответ модели."""
    if keyword_cat == CATEGORY_NIW:
        return CATEGORY_NIW
    if model_cat in VALID_CATEGORIES:
        if (
            keyword_cat == CATEGORY_EB2_STANDARD
            and model_cat == CATEGORY_UNCLEAR
        ):
            return CATEGORY_EB2_STANDARD
        return model_cat
    return keyword_cat if keyword_cat in VALID_CATEGORIES else CATEGORY_UNCLEAR


def _tsv_cell(value: str) -> str:
    """Одна ячейка TSV: без переводов строк и табов."""
    return value.replace("\n", " ").replace("\t", " ").strip()


def _json_text_field(value: object, *, empty_fallback: str) -> str:
    """Строковое поле из JSON: null/не-строка → fallback, иначе очищенный текст."""
    if not isinstance(value, str):
        return empty_fallback
    cleaned = _tsv_cell(value)
    return cleaned if cleaned else empty_fallback


def _normalize_category(value: object) -> str:
    """Приводит поле category из JSON к одной из VALID_CATEGORIES."""
    if not isinstance(value, str):
        return CATEGORY_UNCLEAR
    cleaned = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "niw": CATEGORY_NIW,
        "national_interest_waiver": CATEGORY_NIW,
        "eb2_niw": CATEGORY_NIW,
        "eb2_standard": CATEGORY_EB2_STANDARD,
        "eb2": CATEGORY_EB2_STANDARD,
        "standard": CATEGORY_EB2_STANDARD,
        "advanced_degree": CATEGORY_EB2_STANDARD,
        "exceptional_ability": CATEGORY_EB2_STANDARD,
        "unclear": CATEGORY_UNCLEAR,
        "unknown": CATEGORY_UNCLEAR,
        "other": CATEGORY_UNCLEAR,
    }
    return aliases.get(cleaned, CATEGORY_UNCLEAR)


def analyze_case(
    client: OpenAI, model: str, document_text: str
) -> tuple[str, str, str]:
    """Отправляет текст в OpenAI; возвращает (category, profession, denial).

    category сначала считается эвристикой, затем уточняется полем JSON от модели.
    """
    keyword_cat = classify_eb2_subtype(document_text)
    text = document_text.strip()
    if not text:
        return (
            keyword_cat,
            "не применимо",
            "Текст из PDF извлечь не удалось (возможно, скан без OCR).",
        )

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_JSON},
            {"role": "user", "content": USER_TEMPLATE_JSON.format(text=text)},
        ],
        temperature=0.2,
        max_tokens=2048,
    )
    choice = resp.choices[0].message.content
    if not choice:
        return (keyword_cat, "не указано", "Пустой ответ модели.")

    try:
        data = json.loads(choice)
    except json.JSONDecodeError:
        return (keyword_cat, "не указано", _tsv_cell(choice))

    if not isinstance(data, dict):
        return (keyword_cat, "не указано", _tsv_cell(choice))

    category = merge_category(keyword_cat, _normalize_category(data.get("category")))
    profession_s = _json_text_field(
        data.get("profession"), empty_fallback="не указано в тексте"
    )
    denial_s = _json_text_field(
        data.get("denial"), empty_fallback="Ответ модели без поля denial."
    )
    return (category, profession_s, denial_s)


def organize_pdf_copy(
    pdf_path: Path, classified_root: Path, category: str, rel_name: str
) -> None:
    """Копирует PDF в classified/<category>/… с сохранением относительного пути."""
    dest = classified_root / category / rel_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != pdf_path.resolve():
        shutil.copy2(pdf_path, dest)


def summary_filename_for_topic(topic: str) -> str:
    """Имя TSV по коду темы AAO: summary_18.txt."""
    label = topic.strip() or "unknown"
    safe = re.sub(r"[^\w.-]+", "_", label, flags=re.UNICODE)
    return f"{OUTPUT_FILENAME_PREFIX}{safe}.txt"


def topic_from_rel_path(rel_name: str) -> str | None:
    """Первый сегмент относительного пути, если это код темы (18, All, …)."""
    parts = Path(rel_name).parts
    if parts and _TOPIC_DIR_RE.fullmatch(parts[0]):
        return parts[0]
    return None


def topic_from_pdf_dir(pdf_dir: Path, default_pdf_dir: Path) -> str | None:
    """Код темы из --dir: …/pdfs/18, …/pdfs/18/2025 или каталог с именем 18."""
    try:
        rel = pdf_dir.relative_to(default_pdf_dir)
        if rel.parts and _TOPIC_DIR_RE.fullmatch(rel.parts[0]):
            return rel.parts[0]
    except ValueError:
        pass
    if _TOPIC_DIR_RE.fullmatch(pdf_dir.name):
        return pdf_dir.name
    if _YEAR_DIR_RE.fullmatch(pdf_dir.name) and _TOPIC_DIR_RE.fullmatch(
        pdf_dir.parent.name
    ):
        return pdf_dir.parent.name
    return None


def resolve_topic(
    *,
    pdf_dir: Path,
    default_pdf_dir: Path,
    rel_name: str,
) -> str:
    """Код темы для имени summary_*.txt; unknown, если вывести нельзя."""
    return (
        topic_from_rel_path(rel_name)
        or topic_from_pdf_dir(pdf_dir, default_pdf_dir)
        or "unknown"
    )


def default_summary_out_dir(pdf_dir: Path, default_pdf_dir: Path) -> Path:
    """Куда класть summary_<topic>.txt без --output."""
    if pdf_dir == default_pdf_dir or default_pdf_dir in pdf_dir.parents:
        return USCIS_PDFS_DIR
    return pdf_dir


def main() -> None:
    """Точка входа: разбор аргументов, проверки окружения, обход PDF, запись результата."""
    # Подгружаем OPENAI_* из .env в корне проекта (удобно при запуске scripts/... напрямую).
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_PDF_DIR,
        help=f"Каталог с PDF (по умолчанию: {DEFAULT_PDF_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Один файл результата на весь прогон. Без флага: "
            "files/uscis_pdfs/summary_<topic>.txt (отдельный файл на каждую тему)"
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        help="Модель OpenAI (или OPENAI_MODEL)",
    )
    parser.add_argument(
        "--organize",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Копировать PDF в files/uscis_pdfs/classified/<niw|eb2_standard|unclear>/… "
            "(по умолчанию включено; отключить: --no-organize)"
        ),
    )
    parser.add_argument(
        "--classified-dir",
        type=Path,
        default=None,
        help=(
            "Каталог для раскладки по category "
            f"(по умолчанию: {USCIS_PDFS_DIR / CLASSIFIED_DIRNAME})"
        ),
    )
    args = parser.parse_args()

    pdf_dir: Path = args.dir.resolve()
    if not pdf_dir.is_dir():
        print(f"Каталог не найден: {pdf_dir}", file=sys.stderr)
        sys.exit(1)

    # Ключ не храним в коде: переменная окружения или строка в .env (см. load_dotenv выше).
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Задайте переменную окружения OPENAI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    default_pdf_dir = DEFAULT_PDF_DIR.resolve()
    summary_dir = default_summary_out_dir(pdf_dir, default_pdf_dir)
    classified_root = (
        args.classified_dir.resolve()
        if args.classified_dir
        else (USCIS_PDFS_DIR / CLASSIFIED_DIRNAME).resolve()
    )

    # sorted() даёт воспроизводимый порядок; *.pdf во всех подпапках тем (18/, 19/, …).
    pdfs = sorted(
        p for p in pdf_dir.rglob("*.pdf") if p.is_file()
    )
    if not pdfs:
        print(f"В {pdf_dir} нет файлов .pdf")
        sys.exit(0)
    if MAX_PDF_FILES_FOR_TEST is not None:
        pdfs = pdfs[:MAX_PDF_FILES_FOR_TEST]
        print(
            f"Режим теста: обрабатывается не более {len(pdfs)} PDF (MAX_PDF_FILES_FOR_TEST).",
            flush=True,
        )

    # Один клиент на весь прогон — переиспользование соединений там, где это поддерживает SDK.
    client = OpenAI(api_key=api_key)

    lines_by_topic: dict[str, list[str]] = {}
    counts = {
        CATEGORY_NIW: 0,
        CATEGORY_EB2_STANDARD: 0,
        CATEGORY_UNCLEAR: 0,
    }
    for i, pdf_path in enumerate(pdfs, 1):
        try:
            name = str(pdf_path.relative_to(pdf_dir))
        except ValueError:
            name = pdf_path.name
        topic = resolve_topic(
            pdf_dir=pdf_dir,
            default_pdf_dir=default_pdf_dir,
            rel_name=name,
        )
        print(f"[{i}/{len(pdfs)}] [{topic}] {name}", flush=True)
        category = CATEGORY_UNCLEAR
        try:
            raw = extract_pdf_text(pdf_path)
            category, profession, denial = analyze_case(client, args.model, raw)
        except Exception as e:
            # Один битый файл не должен валить весь прогон: пишем причину в ту же таблицу.
            profession = "—"
            denial = f"Ошибка обработки: {e}"
        counts[category] = counts.get(category, 0) + 1
        if args.organize:
            try:
                organize_pdf_copy(pdf_path, classified_root, category, name)
            except OSError as e:
                denial = f"{denial} | Не удалось скопировать в classified: {e}"
        lines_by_topic.setdefault(topic, []).append(
            f"{name}\t{category}\t{_tsv_cell(profession)}\t{_tsv_cell(denial)}",
        )

    written: list[Path] = []
    if args.output is not None:
        out_path = args.output
        # --output: один файл; строки всех тем подряд в порядке кодов темы.
        all_lines = [
            line
            for topic in sorted(lines_by_topic, key=str)
            for line in lines_by_topic[topic]
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(all_lines) + "\n", encoding="utf-8")
        written.append(out_path)
        print(f"Записано строк: {len(all_lines)} -> {out_path}")
    else:
        summary_dir.mkdir(parents=True, exist_ok=True)
        for topic in sorted(lines_by_topic, key=str):
            topic_lines = lines_by_topic[topic]
            out_path = summary_dir / summary_filename_for_topic(topic)
            # Полная перезапись файла темы: повторный запуск заменяет сводку целиком.
            out_path.write_text("\n".join(topic_lines) + "\n", encoding="utf-8")
            written.append(out_path)
            print(f"Записано строк: {len(topic_lines)} -> {out_path}")

    print(
        "Категории: "
        f"niw={counts.get(CATEGORY_NIW, 0)}, "
        f"eb2_standard={counts.get(CATEGORY_EB2_STANDARD, 0)}, "
        f"unclear={counts.get(CATEGORY_UNCLEAR, 0)}"
    )
    if args.organize:
        print(f"Копии PDF: {classified_root}/<category>/…")


if __name__ == "__main__":
    main()
