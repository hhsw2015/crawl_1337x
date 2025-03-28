from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import csv
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import os
import subprocess
import logging
import random

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.82 Safari/537.36",
]

MAX_RETRIES = 3
RETRY_DELAY = 5
MAX_WORKERS = 10
COMMIT_INTERVAL = 10
MAX_CONSECUTIVE_FAILURES = 5
BASE_URL = "https://1337x.st"

def init_csv(username):
    csv_file = f"{username}.csv"
    if not os.path.exists(csv_file):
        with open(csv_file, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(["page_number", "sub_page_id", "title", "file_size", "category", "magnet_link"])
        logging.info(f"Initialized new CSV file: {csv_file}")
    else:
        logging.info(f"CSV file '{csv_file}' already exists, skipping initialization")
    return csv_file

def load_existing_ids(csv_file):
    existing_ids = set()
    if os.path.exists(csv_file):
        with open(csv_file, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                existing_ids.add(row["sub_page_id"])
    return existing_ids

def git_sync_and_commit(csv_file, message):
    try:
        subprocess.run(["git", "config", "--global", "user.email", "hhsw2015@gmail.com"], check=True)
        subprocess.run(["git", "config", "--global", "user.name", "hhsw2015"], check=True)
        subprocess.run(["git", "pull", "origin", "main"], check=True)
        logging.info("Successfully pulled latest changes from remote repository")
        subprocess.run(["git", "add", csv_file], check=True)
        result = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
        if result.returncode == 0:
            subprocess.run(["git", "push"], check=True)
            logging.info(f"Git commit successful: {message}")
        else:
            logging.warning(f"No changes to commit: {result.stderr}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Git error: {e.stderr}")
        raise

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def get_torrent_page(username, page_num):
    url = f"{BASE_URL}/{username}-torrents/{page_num}/"
    driver = get_driver()
    for attempt in range(MAX_RETRIES):
        try:
            driver.get(url)
            time.sleep(5)  # 等待页面加载
            if "403" in driver.title or "Forbidden" in driver.title:
                raise Exception("403 Forbidden encountered")
            html = driver.page_source
            soup = BeautifulSoup(html, 'html.parser')
            # 调试：保存页面内容
            with open(f"debug_page_{page_num}.html", "w", encoding="utf-8") as f:
                f.write(html)
            logging.info(f"Saved debug HTML for page {page_num}")
            driver.quit()
            return soup
        except Exception as e:
            logging.error(f"Error fetching page {url}: {e}")
            driver.quit()
            if attempt < MAX_RETRIES - 1:
                logging.info(f"Retrying {url} ({attempt + 1}/{MAX_RETRIES}) after {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
            else:
                logging.error(f"Max retries reached for {url}. Skipping.")
                return None

def extract_torrent_links(soup, page_num):
    if not soup:
        return []
    
    torrent_links = []
    # 更新解析逻辑，适应可能的结构变化
    table = soup.find("table", class_="table-list")
    if table:
        for index, tr in enumerate(table.find_all("tr")):
            name_td = tr.find("td", class_="coll-1 name")
            if name_td:
                link_tag = name_td.find_all("a")[-1]
                if link_tag and link_tag.get("href"):
                    full_url = f"{BASE_URL}" + link_tag["href"]
                    torrent_links.append((full_url, page_num, index))
    logging.info(f"Page {page_num}: Found {len(torrent_links)} torrent links")
    return torrent_links

def crawl_detail_page(torrent_url, page_num, index, retries=0):
    sub_page_id = torrent_url.split("/torrent/")[1].split("/")[0]
    driver = get_driver()
    try:
        driver.get(torrent_url)
        time.sleep(5)
        if "403" in driver.title or "Forbidden" in driver.title:
            raise Exception("403 Forbidden encountered")
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        title_tag = soup.find("title")
        title = title_tag.text.strip().replace(" | 1337x", "") if title_tag else "N/A"
        magnet_tag = soup.find("a", href=lambda x: x and "magnet:?" in x)
        magnet_link_full = magnet_tag["href"] if magnet_tag else "N/A"
        magnet_link = magnet_link_full.split("&")[0] if magnet_link_full != "N/A" else "N/A"

        file_size = "N/A"
        category = "N/A"
        for ul in soup.find_all("ul", class_="list"):
            for li in ul.find_all("li"):
                if "Total size" in li.text:
                    file_size = li.find("span").text.strip() if li.find("span") else "N/A"
                elif "Category" in li.text:
                    category = li.find("span").text.strip() if li.find("span") else "N/A"

        logging.info(f"Sub-page data - page_num: {page_num}, id: {sub_page_id}, title: {title}")
        driver.quit()
        return {
            "page_number": page_num,
            "sub_page_id": sub_page_id,
            "title": title,
            "file_size": file_size,
            "category": category,
            "magnet_link": magnet_link,
            "index": index
        }
    except Exception as e:
        logging.error(f"Error fetching detail page {torrent_url}: {e}")
        driver.quit()
        if retries < MAX_RETRIES:
            logging.info(f"Retrying {torrent_url} ({retries + 1}/{MAX_RETRIES}) after {RETRY_DELAY} seconds...")
            time.sleep(RETRY_DELAY)
            return crawl_detail_page(torrent_url, page_num, index, retries + 1)
        else:
            logging.error(f"Max retries ({MAX_RETRIES}) reached for {torrent_url}. Terminating program.")
            sys.exit(1)

def crawl_1337x(username, start_page, end_page):
    csv_file = init_csv(username)
    existing_ids = load_existing_ids(csv_file)
    pbar = tqdm(range(start_page, end_page - 1, -1), desc="Crawling pages")
    page_count = 0
    consecutive_failures = 0

    for page_num in pbar:
        soup = get_torrent_page(username, page_num)
        if not soup:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logging.error(f"Consecutive failures reached {MAX_CONSECUTIVE_FAILURES}. Terminating program.")
                sys.exit(1)
            continue
        else:
            consecutive_failures = 0

        torrent_links = extract_torrent_links(soup, page_num)
        if not torrent_links:
            logging.warning(f"No torrents found on page {page_num}")
            continue

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(crawl_detail_page, url, page_num, index): index for url, page_num, index in torrent_links}
            for future in as_completed(futures):
                result = future.result()
                if result and result["sub_page_id"] not in existing_ids:
                    results.append(result)
                    existing_ids.add(result["sub_page_id"])

        if results:
            results.sort(key=lambda x: x["index"])
            with open(csv_file, mode='a', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                for data in results:
                    writer.writerow([data["page_number"], data["sub_page_id"], data["title"],
                                   data["file_size"], data["category"], data["magnet_link"]])

            page_count += 1
            if page_count % COMMIT_INTERVAL == 0:
                git_sync_and_commit(csv_file, f"Update data for pages {page_num + COMMIT_INTERVAL - 1} to {page_num}")

        pbar.update(1)
        time.sleep(3)

    if page_count % COMMIT_INTERVAL != 0 and results:
        git_sync_and_commit(csv_file, f"Final update for pages {start_page} to {end_page}")
    logging.info(f"Crawl completed. Data saved to {csv_file}")

if __name__ == "__main__":
    logging.info("Starting crawl...")
    username = os.getenv("USERNAME", "mLisa")
    start_page = int(os.getenv("START_PAGE", 100))
    end_page = int(os.getenv("END_PAGE", 1))
    crawl_1337x(username, start_page, end_page)
