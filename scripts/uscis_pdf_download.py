"""Загрузка PDF со страницы USCIS AAO non-precedent decisions.

Скрипт обходит страницы списка решений, находит ссылки на .pdf и сохраняет
файлы в files/uscis_pdfs/pdfs/<topic>/<year>/; успешные URL пишутся в
files/uscis_pdfs/pdf_urls.txt.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from urllib.parse import urlencode, urljoin, urlparse, urlunparse, unquote

import requests
from bs4 import BeautifulSoup

# Корень проекта (родитель папки scripts/) — пути к files/ не зависят от cwd
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Каталог пакета: метаданные (pdf_urls.txt) в корне; PDF — в pdfs/<topic>/<year>/
USCIS_PDFS_ROOT = os.path.join(PROJECT_ROOT, "files", "uscis_pdfs")
PDF_OUTPUT_DIR = os.path.join(USCIS_PDFS_ROOT, "pdfs")

LIST_PAGE_URL = (
    "https://www.uscis.gov/administrative-appeals/aao-decisions/"
    "aao-non-precedent-decisions"
)
# Тема по умолчанию: I-140 Advanced Degree / Exceptional Ability / NIW (см. README)
DEFAULT_TOPIC = "18"
PDF_URLS_FILENAME = (
    "pdf_urls.txt"  # успешные URL, файл очищается в начале каждого запуска
)
# Пауза между запросами PDF, чтобы не перегружать сайт
REQUEST_DELAY_SEC = 0.5
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}
PDF_MAGIC = b"%PDF-"


def _is_pdf_bytes(data: bytes) -> bool:
    """True, если содержимое начинается с сигнатуры PDF."""
    return data.startswith(PDF_MAGIC)


def _select_options(soup: BeautifulSoup, name: str) -> dict[str, str]:
    """value → подпись option в select ``name``."""
    select = soup.select_one(f'select[name="{name}"]')
    if not select:
        raise RuntimeError(f"На странице USCIS не найден select фильтра {name}")
    options: dict[str, str] = {}
    for opt in select.find_all("option"):
        value = opt.get("value")
        if value is None:
            continue
        options[value] = opt.get_text(strip=True)
    return options


def fetch_filter_maps(
    headers: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Возвращает (год→Drupal y, topic_id→название) с формы списка AAO."""
    response = requests.get(LIST_PAGE_URL, headers=headers, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    year_by_label: dict[str, str] = {}
    for value, label in _select_options(soup, "y").items():
        if value == "All" or not label.isdigit():
            continue
        year_by_label[label] = value

    topic_by_id = _select_options(soup, "uri_1")
    if not year_by_label:
        raise RuntimeError("Не удалось прочитать список годов из фильтра USCIS")
    if not topic_by_id:
        raise RuntimeError("Не удалось прочитать список тем из фильтра USCIS")
    return year_by_label, topic_by_id


def build_list_url(year: int, topic: str, headers: dict[str, str]) -> str:
    """Собирает URL списка по ``--year`` и ``--topic``."""
    year_map, topic_map = fetch_filter_maps(headers)
    calendar_year = str(year)
    if calendar_year not in year_map:
        available = ", ".join(sorted(year_map.keys(), reverse=True))
        raise SystemExit(
            f"Год {calendar_year} недоступен в фильтре USCIS. Доступны: {available}"
        )
    if topic not in topic_map:
        raise SystemExit(
            f"Тема {topic} недоступна. См. список тем в README (код --topic)."
        )

    params = {
        "uri_1": topic,
        "m": "All",
        "y": year_map[calendar_year],
        "items_per_page": "100",
    }
    topic_label = topic_map[topic]
    print(f"Фильтр: год {calendar_year}, тема {topic} — {topic_label}")
    return urlunparse(urlparse(LIST_PAGE_URL)._replace(query=urlencode(params)))


def download_pdfs(url: str, output_dir: str) -> tuple[int, int, int]:
    """Скачивает все PDF со страницы списка и со всех следующих страниц пагинации.

    Пагинация на сайте — Drupal mini-pager: следующая страница задаётся ссылкой
    ``<a rel="next" href="...">``. На последней странице этой ссылки нет.
    Уже существующие непустые файлы в ``output_dir`` не скачиваются повторно.
    Повторные ссылки на один и тот же PDF (URL или имя файла) пропускаются.
    Ответ без сигнатуры ``%PDF-`` не сохраняется.

    :param url: URL первой страницы списка
    :param output_dir: каталог для сохранения PDF (обычно ``.../uscis_pdfs/pdfs/<topic>/<year>``); ``pdf_urls.txt`` — в ``USCIS_PDFS_ROOT``
    :return: (скачано, пропущено как уже есть, ошибок)
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    urls_log_path = os.path.join(USCIS_PDFS_ROOT, PDF_URLS_FILENAME)
    headers = DEFAULT_HEADERS
    downloaded = 0
    skipped = 0
    failed = 0
    # Защита от зацикливания, если пагинация вернёт тот же URL
    seen_page_urls: set[str] = set()
    # Один PDF — одна строка в логе (на выдаче AAO ссылки иногда дублируются)
    seen_pdf_urls: set[str] = set()
    seen_pdf_names: set[str] = set()
    current_url: str | None = url
    page_num = 1

    # Пустой файл в начале запуска; дальше URL существующих и новых PDF
    with open(urls_log_path, "w", encoding="utf-8") as url_log:
        while current_url:
            if current_url in seen_page_urls:
                print(f"Повтор URL, остановка: {current_url}", file=sys.stderr)
                break
            seen_page_urls.add(current_url)

            print(f"Страница {page_num}: {current_url}")
            response = requests.get(current_url, headers=headers, timeout=60)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            # После редиректа относительные ссылки в HTML нужно резолвить от финального URL
            page_base = response.url
            for link in soup.find_all("a", href=True):
                href = link["href"]
                # На странице могут быть иные ссылки; нас интересуют только PDF
                if not href.lower().endswith(".pdf"):
                    continue
                file_url = urljoin(page_base, href)
                if file_url in seen_pdf_urls:
                    continue
                # Имя файла — последний сегмент пути (как на сервере)
                base_name = unquote(href.split("/")[-1])
                if base_name in seen_pdf_names:
                    seen_pdf_urls.add(file_url)
                    continue
                file_name = os.path.join(output_dir, base_name)
                seen_pdf_urls.add(file_url)
                seen_pdf_names.add(base_name)

                if os.path.isfile(file_name) and os.path.getsize(file_name) > 0:
                    print(f"Уже есть, пропуск: {file_name}")
                    skipped += 1
                    url_log.write(file_url + "\n")
                    continue

                print(f"Загрузка: {file_url}")
                try:
                    # PDF обычно крупнее HTML — даём больший таймаут
                    file_data = requests.get(file_url, headers=headers, timeout=120)
                    file_data.raise_for_status()
                    content = file_data.content
                    if not _is_pdf_bytes(content):
                        ctype = (file_data.headers.get("Content-Type") or "").split(";")[0].strip()
                        raise ValueError(
                            f"ответ не PDF (magic={content[:8]!r}, Content-Type={ctype!r})"
                        )
                    with open(file_name, "wb") as f:
                        f.write(content)
                    downloaded += 1
                    url_log.write(file_url + "\n")
                except Exception as e:
                    failed += 1
                    print(f"Ошибка при загрузке {file_url}: {e}", file=sys.stderr)
                time.sleep(REQUEST_DELAY_SEC)

            # Drupal pager: rel="next" ведёт на следующую страницу того же списка
            next_a = soup.select_one('a[rel="next"]')
            if not next_a or not next_a.get("href"):
                break
            current_url = urljoin(page_base, next_a["href"])
            page_num += 1

    return downloaded, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="Календарный год фильтра (по умолчанию — текущий)",
    )
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help=(
            f"Код темы фильтра (по умолчанию {DEFAULT_TOPIC} = I-140 Advanced Degree / "
            "NIW). All = все темы. Список — в README."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=PDF_OUTPUT_DIR,
        help=(
            f"Базовый каталог для PDF (по умолчанию: {PDF_OUTPUT_DIR}); "
            "файлы пишутся в подпапки <topic>/<year>"
        ),
    )
    args = parser.parse_args()

    topic = str(args.topic)
    year = int(args.year)
    out_dir = os.path.join(args.output_dir, topic, str(year))
    list_url = build_list_url(year, topic, DEFAULT_HEADERS)
    n, skipped, err = download_pdfs(list_url, out_dir)
    print(f"Готово! Скачано файлов: {n} → {out_dir}")
    if skipped:
        print(f"Пропущено (уже есть): {skipped}")
    if err:
        print(f"Не удалось скачать: {err} файлов")


if __name__ == "__main__":
    main()
