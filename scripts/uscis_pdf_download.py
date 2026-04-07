"""Загрузка PDF со страницы USCIS AAO non-precedent decisions."""
import os
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Корень проекта (родитель папки scripts/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Настройки
TARGET_URL = (
    "https://www.uscis.gov/administrative-appeals/aao-decisions/"
    "aao-non-precedent-decisions?uri_1=18&m=All&y=1&items_per_page=100"
)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "uscis_pdfs")


def download_pdfs(url: str, output_dir: str) -> None:
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    links = soup.find_all("a", href=True)

    for link in links:
        href = link["href"]
        if href.endswith(".pdf"):
            file_url = urljoin(url, href)
            file_name = os.path.join(output_dir, href.split("/")[-1])

            print(f"Загрузка: {file_url}")
            try:
                file_data = requests.get(file_url, headers=headers, timeout=120)
                file_data.raise_for_status()
                with open(file_name, "wb") as f:
                    f.write(file_data.content)
            except Exception as e:
                print(f"Ошибка при загрузке {file_url}: {e}", file=sys.stderr)


if __name__ == "__main__":
    download_pdfs(TARGET_URL, OUTPUT_DIR)
    print("Готово!")
