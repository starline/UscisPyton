"""Извлечение текста из PDF и краткое описание причины отказа через OpenAI.

Общий процесс (конвейер):
  1) Находим все файлы *.pdf в каталоге (по умолчанию files/uscis_pdfs).
  2) Для каждого PDF последовательно извлекаем текст со всех страниц (без OCR — только
     встроенный текст; отсканированные страницы без слоя текста дадут пустой результат).
  3) Извлечённый текст отправляем в Chat Completions API OpenAI с инструкцией выделить
     суть отказа одним предложением на русском.
  4) Для каждого файла формируем одну строку: «имя.pdf<TAB>результат» и записываем все
     строки в summary_denids.txt (кодировка UTF-8, в конце файла перевод строки).

Переменные окружения:
  OPENAI_API_KEY — ключ API (обязательно)
  OPENAI_MODEL   — модель чата (по умолчанию gpt-4o-mini)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI
from pypdf import PdfReader

# Корень репозитория: scripts/ -> родитель = проект; так пути не зависят от текущей папки запуска.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Каталог по умолчанию совпадает с тем, куда uscis_pdf_download.py кладёт решения.
DEFAULT_PDF_DIR = PROJECT_ROOT / "files" / "uscis_pdfs"
OUTPUT_FILENAME = "summary_denids.txt"

# Системное сообщение задаёт «роль» модели и формат ответа (одно предложение, русский язык).
SYSTEM_PROMPT = (
    "Ты помощник по анализу административных решений USCIS/AAO. "
    "Отвечай только одним предложением на русском языке, без кавычек в начале и конце."
)

# Пользовательский промпт: в {text} подставляется тело документа. Явно просим различать
# случай «есть отказ» / «отказа нет» / «недостаточно данных», чтобы выход был предсказуемым.
USER_TEMPLATE = """Ниже текст документа (решение по делу). Задача:
1) Определи, есть ли отказ в удовлетворении апелляции/ходатайства или иное решение об отказе.
2) Если да — опиши главную причину отказа ОДНИМ предложением.
3) Если отказа нет или текст не позволяет судить — одним предложением укажи это кратко.

Текст документа:
---
{text}
---
"""


def extract_pdf_text(path: Path) -> str:
    """Собирает текст со всех страниц PDF в одну строку.

    Порядок страниц — как в файле. Между страницами вставляется перевод строки, чтобы не
    слипались последнее слово одной страницы и первое слово следующей (если извлечение
    не добавляет пробелы). Пустые страницы пропускаются.
    """
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def summarize_denial(client: OpenAI, model: str, document_text: str) -> str:
    """Отправляет текст решения в OpenAI и возвращает одну строку-резюме причины отказа.

    Если после извлечения текста ничего нет — API не вызываем: сразу возвращаем понятное
    сообщение (часто это скан без текстового слоя).

    Длинный текст обрезается до 120_000 символов, чтобы уложиться в типичный лимит контекста
    модели и не получить ошибку от API; для очень больших PDF при необходимости логику
    можно усложнить (чанки, суммаризация по частям).

    temperature=0.2 — слегка снижаем случайность, чтобы формулировки по похожим делам
    были стабильнее.

    Ответ модели нормализуется: схлопываем пробелы и переводы строк в одну строку —
    так проще писать в TSV-строку (одна строка на файл в итоговом файле).
    """
    text = document_text.strip()
    if not text:
        return "Текст из PDF извлечь не удалось (возможно, скан без OCR)."

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(text=text[:120_000])},
        ],
        temperature=0.2,
    )
    choice = resp.choices[0].message.content
    if not choice:
        return "Пустой ответ модели."
    return " ".join(choice.strip().split())


def main() -> None:
    """Точка входа: разбор аргументов, проверки окружения, обход PDF, запись результата."""
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
        help="Файл результата (по умолчанию: <каталог>/summary_denids.txt)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        help="Модель OpenAI (или OPENAI_MODEL)",
    )
    args = parser.parse_args()

    pdf_dir: Path = args.dir
    if not pdf_dir.is_dir():
        print(f"Каталог не найден: {pdf_dir}", file=sys.stderr)
        sys.exit(1)

    # Ключ не храним в коде; ожидается экспорт в окружении или .env через оболочку/IDE.
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Задайте переменную окружения OPENAI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    # Итоговый файл по умолчанию лежит рядом с PDF, чтобы один каталог = один «пакет» анализа.
    out_path = args.output if args.output else pdf_dir / OUTPUT_FILENAME

    # sorted() даёт воспроизводимый порядок строк в summary при повторных запусках.
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print(f"В {pdf_dir} нет файлов .pdf")
        sys.exit(0)

    # Один клиент на весь прогон — переиспользование соединений там, где это поддерживает SDK.
    client = OpenAI(api_key=api_key)

    lines: list[str] = []
    for i, pdf_path in enumerate(pdfs, 1):
        name = pdf_path.name
        print(f"[{i}/{len(pdfs)}] {name}", flush=True)
        try:
            raw = extract_pdf_text(pdf_path)
            summary = summarize_denial(client, args.model, raw)
        except Exception as e:
            # Один битый файл не должен валить весь прогон: пишем причину в ту же таблицу.
            summary = f"Ошибка обработки: {e}"
        # Формат строки: TSV — имя файла, табуляция, ответ (без внутренних табов/переносов).
        safe_summary = summary.replace("\n", " ").replace("\t", " ").strip()
        lines.append(f"{name}\t{safe_summary}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Полная перезапись файла: повторный запуск заменяет сводку целиком, а не дублирует.
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано строк: {len(lines)} -> {out_path}")


if __name__ == "__main__":
    main()
