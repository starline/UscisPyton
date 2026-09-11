"""Извлечение текста из PDF и краткое описание причины отказа через OpenAI.

Общий процесс (конвейер):
  1) Находим файлы *.pdf в каталоге и подпапках тем/годов (по умолчанию files/uscis_pdfs/pdfs);
     при тестовом лимите берём только первые N по имени (см. MAX_PDF_FILES_FOR_TEST).
  2) Для каждого PDF последовательно извлекаем текст (не более первых 12 000 символов,
     считая переводы строк между страницами; дальнейшие страницы не читаются) — без OCR,
     только встроенный текст; отсканированные страницы без слоя текста дадут пустой результат.
  3) Извлечённый текст отправляем в Chat Completions API OpenAI: в JSON — сведения о бенефициаре
     (профессия, должность, сфера) и развёрнутое описание отказа с указанием, какие доказательства
     отклонены или признаны недостаточными (на русском).
  4) Для каждого файла одна строка TSV: «имя.pdf<TAB>профессия/должность/сфера<TAB>причина отказа и доказательства»
     → summary_denials.txt (UTF-8).

Переменные окружения:
  OPENAI_API_KEY — ключ API (обязательно)
  OPENAI_MODEL   — модель чата (по умолчанию gpt-4o-mini)

Файл .env в корне проекта (рядом с run.py) подхватывается автоматически при запуске скрипта.
"""

from __future__ import annotations

import argparse
import json
import os
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
OUTPUT_FILENAME = "summary_denials.txt"
# Максимум символов, извлекаемых из PDF (начало документа; перевод строки между страницами входит в лимит).
PDF_TEXT_MAX_CHARS = 12_000
# Для тестов: не более стольких PDF подряд (после sorted по имени). None — обработать все файлы в каталоге.
MAX_PDF_FILES_FOR_TEST: int | None = None

# Ответ только JSON (режим json_object у API); поля парсятся в analyze_case.
SYSTEM_PROMPT_JSON = (
    "Ты помощник по анализу административных решений USCIS/AAO. "
    "Отвечай только одним JSON-объектом на русском языке в значениях полей, без текста до или после JSON. "
    "Поля profession и denial могут быть из нескольких предложений, если нужно для полноты."
)

# В {text} — фрагмент решения. Модель возвращает profession + denial для строки summary_denials.txt.
USER_TEMPLATE_JSON = """Ниже фрагмент текста решения по делу (петиция/апелляция и т.д.).

Сделай:
1) profession: для бенефициара петиции укажи в одном связном тексте (пара предложений допустимо):
   — профессию / специальность (occupation);
   — в какой должности (job title) он работает или будет работать, если это сказано в тексте;
   — в какой сфере, отрасли или области деятельности (field / industry), если это есть в тексте.
   Если чего-то нет в отрывке — явно напиши, что не указано. Если бенефициар не идентифицируется — «не указано в тексте».

2) denial: опиши подробно на русском:
   — есть ли отказ в удовлетворении апелляции/ходатайства и в чём суть решения;
   — какие именно доказательства, доводы, документы или виды доказательств были отклонены, не приняты, не засчитаны или признаны недостаточными/неубедительными (перечисли по смыслу текста: например отчёты, письма, экспертные заключения, публикации, критерии EB-1/NIW и т.д., если они фигурируют);
   — если отказа нет или текст обрывается — так и укажи.

Текст документа:
---
{text}
---

Верни строго JSON вида:
{{"profession": "...", "denial": "..."}}
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


def _tsv_cell(value: str) -> str:
    """Одна ячейка TSV: без переводов строк и табов."""
    return value.replace("\n", " ").replace("\t", " ").strip()


def analyze_case(client: OpenAI, model: str, document_text: str) -> tuple[str, str]:
    """Отправляет текст в OpenAI; возвращает (профессия/должность/сфера бенефициара, развёрнутое описание отказа).

    Профессия и отказ приходят в одном JSON-ответе (response_format json_object).
    """
    text = document_text.strip()
    if not text:
        return (
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
        return ("не указано", "Пустой ответ модели.")

    try:
        data = json.loads(choice)
    except json.JSONDecodeError:
        return ("не указано", _tsv_cell(choice))

    prof = data.get("profession")
    denial = data.get("denial")
    profession_s = _tsv_cell(prof) if isinstance(prof, str) else _tsv_cell(str(prof))
    if not profession_s:
        profession_s = "не указано в тексте"
    denial_s = _tsv_cell(denial) if isinstance(denial, str) else _tsv_cell(str(denial))
    if not denial_s:
        denial_s = "Ответ модели без поля denial."
    return (profession_s, denial_s)


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
            "Файл результата (по умолчанию: files/uscis_pdfs/summary_denials.txt "
            "или <каталог>/summary_denials.txt при своём --dir)"
        ),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        help="Модель OpenAI (или OPENAI_MODEL)",
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

    # По умолчанию при стандартном каталоге — рядом с pdf_urls.txt; иначе — в том же каталоге, что и PDF.
    default_pdf_dir = DEFAULT_PDF_DIR.resolve()
    out_path = args.output if args.output else (
        USCIS_PDFS_DIR / OUTPUT_FILENAME
        if pdf_dir == default_pdf_dir
        else pdf_dir / OUTPUT_FILENAME
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

    lines: list[str] = []
    for i, pdf_path in enumerate(pdfs, 1):
        try:
            name = str(pdf_path.relative_to(pdf_dir))
        except ValueError:
            name = pdf_path.name
        print(f"[{i}/{len(pdfs)}] {name}", flush=True)
        try:
            raw = extract_pdf_text(pdf_path)
            profession, denial = analyze_case(client, args.model, raw)
        except Exception as e:
            # Один битый файл не должен валить весь прогон: пишем причину в ту же таблицу.
            profession = "—"
            denial = f"Ошибка обработки: {e}"
        lines.append(
            f"{name}\t{_tsv_cell(profession)}\t{_tsv_cell(denial)}",
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Полная перезапись файла: повторный запуск заменяет сводку целиком, а не дублирует.
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано строк: {len(lines)} -> {out_path}")


if __name__ == "__main__":
    main()
