import argparse
import html
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime

import feedparser
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import certifi
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================================
# Logging
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================================
# Data Model
# ============================================================================

@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: str
    published: str
    url: str
    abstract: str


# ============================================================================
# Session Factory
# ============================================================================

def create_session():

    session = requests.Session()

    retry_strategy = Retry(
        total=1,
        backoff_factor=1,
        status_forcelist=[ 500, 502, 503, 504],
        allowed_methods=["GET"]
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0 Safari/537.36"
    )
})
    session.verify = certifi.where()
    return session


# ============================================================================
# Fetch arXiv Results
# ============================================================================

def fetch_arxiv_results(
    session,
    query,
    date_from,
    date_to,
    page_start=0,
    page_size=50
):

    base_url = "https://arxiv.org/api/query"

    date_from_str = date_from.strftime("%Y%m%d0000")
    date_to_str = date_to.strftime("%Y%m%d2359")

    search_query = (
        f'(ti:"{query}" OR abs:"{query}") '
        f'AND submittedDate:[{date_from_str} TO {date_to_str}]'
    )

    params = {
        "search_query": search_query,
        "start": page_start,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending"
    }

    try:

        logger.info(
            f"Fetching papers | start={page_start} | size={page_size}"
        )
        time.sleep(5)

        response = session.get(
            base_url,
            params=params,
            timeout=30,
            verify=False
        )

        response.raise_for_status()

    except requests.RequestException as e:

        logger.error(f"Failed to retrieve arXiv results: {e}")

        return [], 0

    feed = feedparser.parse(response.text)

    total_results = int(
        getattr(feed.feed, "opensearch_totalresults", 0)
    )

    papers = []

    for entry in feed.entries:

        try:

            paper = Paper(
                arxiv_id=entry.id.split("/abs/")[-1],
                title=html.unescape(entry.title.strip()),
                authors=", ".join(
                    author.name for author in entry.authors
                ),
                published=entry.published,
                url=entry.link,
                abstract=html.unescape(
                    entry.summary.strip()
                )
            )

            papers.append(asdict(paper))

        except Exception as e:
            logger.warning(f"Failed to parse entry: {e}")

    logger.info(
        f"Fetched {len(papers)} papers "
        f"(total_results={total_results})"
    )

    # arXiv API rate-limit recommendation
    time.sleep(3)

    return papers, total_results


# ============================================================================
# Save Result
# ============================================================================

def save_result(
    papers,
    query,
    date_from,
    date_to
):

    if not papers:

        logger.warning("No papers to save.")

        return

    df = pd.DataFrame(papers)

    # Remove duplicates
    df.drop_duplicates(
        subset=["arxiv_id"],
        inplace=True
    )

    # Sort by published date
    df.sort_values(
        by="published",
        ascending=False,
        inplace=True
    )

    query_safe = query.replace(" ", "_")

    date_from_safe = date_from.strftime("%Y%m%d")
    date_to_safe = date_to.strftime("%Y%m%d")

    output_dir = f"./{query_safe}/crawling"

    os.makedirs(output_dir, exist_ok=True)

    filename = (
        f"{output_dir}/"
        f"{date_from_safe}_{date_to_safe}.csv"
    )

    df.to_csv(
        filename,
        index=False,
        encoding="utf-8-sig"
    )

    logger.info(
        f"Saved {len(df)} unique papers to:\n{filename}"
    )


# ============================================================================
# Main
# ============================================================================

def main():

    parser = argparse.ArgumentParser(
        description="Fetch arXiv papers by query and date range."
    )

    parser.add_argument(
        "--query",
        type=str,
        default="quantum computing",
        help="Search query"
    )

    parser.add_argument(
        "--date-from",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--date-to",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="Results per request"
    )

    args = parser.parse_args()

    query = args.query

    date_from = datetime.strptime(
        args.date_from,
        "%Y-%m-%d"
    )

    date_to = datetime.strptime(
        args.date_to,
        "%Y-%m-%d"
    )

    page_start = 0
    page_size = args.page_size

    all_papers = []

    session = create_session()

    logger.info("Starting arXiv crawler...")

    while True:

        papers, total_results = fetch_arxiv_results(
            session=session,
            query=query,
            date_from=date_from,
            date_to=date_to,
            page_start=page_start,
            page_size=page_size
        )

        if not papers:

            logger.info("No more papers found.")

            break

        all_papers.extend(papers)

        page_start += page_size

        logger.info(
            f"Progress: {len(all_papers)} / {total_results}"
        )

        if page_start >= total_results:
            break

    logger.info("Finished fetching papers.")

    save_result(
        papers=all_papers,
        query=query,
        date_from=date_from,
        date_to=date_to
    )


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    main()