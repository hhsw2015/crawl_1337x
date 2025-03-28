from py1337x import Py1337x
import csv
import time
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import os
import subprocess
import logging
import random

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

MAX_RETRIES = 3
RETRY_DELAY = 5
MAX_WORKERS = 10
COMMIT_INTERVAL = 10
MAX_CONSECUTIVE_FAILURES = 5

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

def fetch_torrent_page(torrents, username, page_num, retries=0):
    try:
        results = torrents.browse(username, page=page_num)
        if not results or not results.get('items'):
            logging.warning(f"No torrents found on page {page_num}")
            return []
        logging.info(f"Page {page_num}: Found {len(results['items'])} torrent links")
        return [(item, page_num, index) for index, item in enumerate(results['items'])]
    except Exception as e:
        logging.error(f"Error fetching page {page_num}: {e}")
        if retries < MAX_RETRIES:
            logging.info(f"Retrying page {page_num} ({retries + 1}/{MAX_RETRIES}) after {RETRY_DELAY} seconds...")
            time.sleep(RETRY_DELAY)
            return fetch_torrent_page(torrents, username, page_num, retries + 1)
        else:
            logging.error(f"Max retries reached for page {page_num}. Skipping.")
            return None

def process_torrent_item(item, page_num, index):
    try:
        sub_page_id = item.get('torrentId', 'N/A')
        title = item.get('name', 'N/A')
        file_size = item.get('size', 'N/A')
        category = item.get('category', 'N/A')
        magnet_link = item.get('magnetLink', 'N/A')

        logging.info(f"Processed - page_num: {page_num}, id: {sub_page_id}, title: {title}")
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
        logging.error(f"Error processing item {item}: {e}")
        return None

def crawl_1337x(username, start_page, end_page):
    # 初始化 py1337x
    torrents = Py1337x()
    csv_file = init_csv(username)
    existing_ids = load_existing_ids(csv_file)
    pbar = tqdm(range(start_page, end_page - 1, -1), desc="Crawling pages")
    page_count = 0
    consecutive_failures = 0

    for page_num in pbar:
        torrent_items = fetch_torrent_page(torrents, username, page_num)
        if torrent_items is None:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logging.error(f"Consecutive failures reached {MAX_CONSECUTIVE_FAILURES}. Terminating program.")
                sys.exit(1)
            continue
        elif not torrent_items:
            consecutive_failures += 1
            continue
        else:
            consecutive_failures = 0

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_torrent_item, item, page_num, index): index 
                       for item, page_num, index in torrent_items}
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
        time.sleep(random.uniform(1, 3))  # 随机延迟避免反爬

    if page_count % COMMIT_INTERVAL != 0 and results:
        git_sync_and_commit(csv_file, f"Final update for pages {start_page} to {end_page}")
    logging.info(f"Crawl completed. Data saved to {csv_file}")

if __name__ == "__main__":
    logging.info("Starting crawl...")
    username = os.getenv("USERNAME", "mLisa")
    start_page = int(os.getenv("START_PAGE", 100))
    end_page = int(os.getenv("END_PAGE", 1))
    crawl_1337x(username, start_page, end_page)
