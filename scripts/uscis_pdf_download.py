"""Загрузка PDF со страницы USCIS AAO non-precedent decisions.

Скрипт обходит страницы списка решений, находит ссылки на .pdf и сохраняет
файлы в files/uscis_pdfs/pdfs; успешные URL пишутся в files/uscis_pdfs/pdf_urls.txt.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urljoin, unquote

import requests
from bs4 import BeautifulSoup

# Корень проекта (родитель папки scripts/) — пути к files/ не зависят от cwd
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Каталог пакета: метаданные (pdf_urls.txt) в корне; сами PDF — в подпапке pdfs/
USCIS_PDFS_ROOT = os.path.join(PROJECT_ROOT, "files", "uscis_pdfs")
PDF_OUTPUT_DIR = os.path.join(USCIS_PDFS_ROOT, "pdfs")

# Стартовый URL списка решений (фильтры в query: месяц, год, число строк на страницу)
TARGET_URL = "https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions?uri_1=18&m=All&y=2&items_per_page=100"
PDF_URLS_FILENAME = (
    "pdf_urls.txt"  # успешные URL, файл очищается в начале каждого запуска
)
# Пауза между запросами PDF, чтобы не перегружать сайт
REQUEST_DELAY_SEC = 0.5


def download_pdfs(url: str, output_dir: str) -> tuple[int, int]:
    """Скачивает все PDF со страницы списка и со всех следующих страниц пагинации.

    Пагинация на сайте — Drupal mini-pager: следующая страница задаётся ссылкой
    ``<a rel="next" href="...">``. На последней странице этой ссылки нет.

    :param url: URL первой страницы списка
    :param output_dir: каталог для сохранения PDF (обычно ``.../uscis_pdfs/pdfs``); ``pdf_urls.txt`` — в ``USCIS_PDFS_ROOT``
    :return: (число успешно скачанных, число неудачных) — ошибки отдельных PDF не прерывают цикл
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    urls_log_path = os.path.join(USCIS_PDFS_ROOT, PDF_URLS_FILENAME)
    # Минимальный User-Agent: часть сайтов отдаёт 403 без «браузерного» заголовка
    headers = {"User-Agent": "Mozilla/5.0"}
    downloaded = 0
    failed = 0
    # Защита от зацикливания, если пагинация вернёт тот же URL
    seen_urls: set[str] = set()
    current_url: str | None = url
    page_num = 1

    # Пустой файл в начале запуска; дальше только успешные URL (append не используем)
    with open(urls_log_path, "w", encoding="utf-8") as url_log:
        while current_url:
            if current_url in seen_urls:
                print(f"Повтор URL, остановка: {current_url}", file=sys.stderr)
                break
            seen_urls.add(current_url)

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
                # Имя файла — последний сегмент пути (как на сервере)
                file_name = os.path.join(output_dir, unquote(href.split("/")[-1]))

                print(f"Загрузка: {file_url}")
                try:
                    # PDF обычно крупнее HTML — даём больший таймаут
                    file_data = requests.get(file_url, headers=headers, timeout=120)
                    file_data.raise_for_status()
                    with open(file_name, "wb") as f:
                        f.write(file_data.content)
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

    return downloaded, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=TARGET_URL,
        help="Стартовый URL списка решений",
    )
    parser.add_argument(
        "--output-dir",
        default=PDF_OUTPUT_DIR,
        help=f"Каталог для PDF (по умолчанию: {PDF_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    n, err = download_pdfs(args.url, args.output_dir)
    print(f"Готово! Скачано файлов: {n}")
    if err:
        print(f"Не удалось скачать: {err} файлов")


if __name__ == "__main__":
    main()
