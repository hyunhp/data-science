import argparse
import logging
import os
import random
import re
import sys
import time

from datetime import datetime
from urllib.parse import urlencode

import xml.etree.ElementTree as ET

import pandas as pd
import requests

import urllib3

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)

# ============================================================================
# Logging
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

# The HTML search UI (arxiv.org/search) returns HTTP 500 for any start > 0,
# so it can never return more than the first page. The export API paginates
# properly and filters by submission date server-side.
API_ENDPOINT = "https://export.arxiv.org/api/query"

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom"
}

OPENSEARCH_TOTAL = (
    "{http://a9.com/-/spec/opensearch/1.1/}totalResults"
)

# arXiv asks for at least 3 seconds between API calls, and still answers
# 429 / 503 fairly often (especially at peak times), so retries back off
# exponentially and honour any Retry-After the server sends.
REQUEST_DELAY = 3

PAGE_SIZE = 200

MAX_RETRY = 6

# Retry backoff: BACKOFF_BASE * 2**attempt, jittered, capped at BACKOFF_CAP.
BACKOFF_BASE = 5

BACKOFF_CAP = 120


class CrawlError(Exception):
    """A page could not be fetched after exhausting retries."""


# ============================================================================
# Session
# ============================================================================

def create_session():

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0 Safari/537.36"
            )
        }
    )

    return session


# ============================================================================
# URL Builder
# ============================================================================

def build_url(
    query: str,
    date_from: datetime,
    date_to: datetime,
    start: int = 0,
    size: int = PAGE_SIZE
):

    # submittedDate is inclusive on both ends, so cover the whole last day.
    date_range = (
        f"[{date_from.strftime('%Y%m%d')}0000"
        f" TO {date_to.strftime('%Y%m%d')}2359]"
    )

    # AND the terms instead of matching the whole query as a phrase, so
    # "Quantum Computing" keeps the broad recall of the old HTML search.
    terms = " AND ".join(
        f"all:{term}"
        for term in query.split()
    )

    search_query = (
        f"{terms} AND submittedDate:{date_range}"
    )

    params = urlencode(
        {
            "search_query": search_query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": start,
            "max_results": size
        }
    )

    return f"{API_ENDPOINT}?{params}"


# ============================================================================
# Fetch
# ============================================================================

def retry_wait(exc, attempt):
    """Seconds to wait before the next attempt.

    Honours the server's Retry-After header when present, otherwise backs off
    exponentially with a little jitter so parallel clients do not resynchronise.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(BACKOFF_CAP, max(1, int(retry_after)))
            except ValueError:
                pass

    backoff = BACKOFF_BASE * (2 ** (attempt - 1))
    return min(BACKOFF_CAP, backoff) + random.uniform(0, 2)


def fetch_page(
    session,
    url
):

    for attempt in range(1, MAX_RETRY + 1):

        try:

            response = session.get(
                url,
                timeout=90,
                verify=False
            )

            # 429 / 503 are transient "come back later" signals, not real
            # failures, so raise to trigger the same backoff path.
            response.raise_for_status()

            return ET.fromstring(response.text)

        except Exception as e:

            if attempt < MAX_RETRY:
                wait = retry_wait(e, attempt)
                logger.warning(
                    f"Fetch failed ({attempt}/{MAX_RETRY}): {e}; "
                    f"retrying in {wait:.0f}s"
                )
                time.sleep(wait)
            else:
                logger.warning(
                    f"Fetch failed ({attempt}/{MAX_RETRY}): {e}; giving up"
                )

    raise CrawlError(f"could not fetch {url}")


# ============================================================================
# Parse Search Result
# ============================================================================

def clean_text(value):

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def parse_entry(entry):

    # id looks like http://arxiv.org/abs/2607.19415v1
    raw_id = clean_text(
        entry.findtext("a:id", namespaces=ATOM_NS)
    )

    arxiv_id = re.sub(
        r"v\d+$",
        "",
        raw_id.split("/abs/")[-1]
    )

    published_raw = clean_text(
        entry.findtext("a:published", namespaces=ATOM_NS)
    )

    published = datetime.strptime(
        published_raw,
        "%Y-%m-%dT%H:%M:%SZ"
    )

    authors = ", ".join(
        clean_text(
            author.findtext("a:name", namespaces=ATOM_NS)
        )
        for author in entry.findall("a:author", namespaces=ATOM_NS)
    )

    return {
        "arxiv_id": arxiv_id,
        "title": clean_text(
            entry.findtext("a:title", namespaces=ATOM_NS)
        ),
        "authors": authors,
        "published": published.strftime("%Y-%m-%d"),
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "abstract": clean_text(
            entry.findtext("a:summary", namespaces=ATOM_NS)
        )
    }


def parse_page(
    root,
    date_from,
    date_to
):

    papers = []

    for entry in root.findall("a:entry", namespaces=ATOM_NS):

        try:

            paper = parse_entry(entry)

            published = datetime.strptime(
                paper["published"],
                "%Y-%m-%d"
            )

            # The API already filters by date, this only guards against
            # boundary surprises.
            if not (
                date_from.date()
                <= published.date()
                <= date_to.date()
            ):
                continue

            papers.append(paper)

        except Exception as e:

            logger.warning(
                f"Failed to parse paper: {e}"
            )

    return papers


# ============================================================================
# Crawl
# ============================================================================

def crawl_arxiv(
    query,
    date_from,
    date_to,
    page_size=PAGE_SIZE
):

    session = create_session()

    start = 0

    total = None

    all_papers = []

    logger.info(
        f"Searching query='{query}' "
        f"{date_from.strftime('%Y-%m-%d')} ~ "
        f"{date_to.strftime('%Y-%m-%d')}"
    )

    while True:

        url = build_url(
            query=query,
            date_from=date_from,
            date_to=date_to,
            start=start,
            size=page_size
        )

        logger.info(
            f"Fetching start={start}"
        )

        try:

            root = fetch_page(
                session,
                url
            )

        except CrawlError as e:

            # Nothing at all came back: let the caller fail and be retried.
            if start == 0:
                raise

            # We already have some pages; keep them rather than lose the run.
            logger.error(
                f"Stopping at start={start} after fetch failure: {e}. "
                f"Keeping {len(all_papers)} collected."
            )

            break

        if total is None:

            total_el = root.find(OPENSEARCH_TOTAL)

            if total_el is not None:

                total = int(total_el.text)

                logger.info(
                    f"Total matching papers={total}"
                )

        entry_count = len(
            root.findall("a:entry", namespaces=ATOM_NS)
        )

        all_papers.extend(
            parse_page(
                root,
                date_from,
                date_to
            )
        )

        logger.info(
            f"Collected={len(all_papers)}"
        )

        if entry_count == 0:
            break

        start += entry_count

        if total is not None and start >= total:
            break

        time.sleep(REQUEST_DELAY)

    return all_papers


# ============================================================================
# Save
# ============================================================================

def save_result(
    papers,
    query,
    date_from,
    date_to
):

    if len(papers) == 0:

        logger.warning(
            "No papers found."
        )

        return

    df = pd.DataFrame(
        papers
    )

    df.drop_duplicates(
        subset=["arxiv_id"],
        inplace=True
    )

    df.sort_values(
        by="published",
        ascending=False,
        inplace=True
    )

    query_safe = query.replace(
        " ",
        "_"
    )

    output_dir = (
        f"./{query_safe}/crawling"
    )

    os.makedirs(
        output_dir,
        exist_ok=True
    )

    filename = (
        f"{output_dir}/"
        f"{date_from.strftime('%Y%m%d')}_"
        f"{date_to.strftime('%Y%m%d')}.csv"
    )

    df.to_csv(
        filename,
        index=False,
        encoding="utf-8-sig"
    )

    logger.info(
        f"Saved {len(df)} papers"
    )

    logger.info(
        filename
    )


# ============================================================================
# Main
# ============================================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--query",
        required=True
    )

    parser.add_argument(
        "--date-from",
        required=True
    )

    parser.add_argument(
        "--date-to",
        required=True
    )

    args = parser.parse_args()

    date_from = datetime.strptime(
        args.date_from,
        "%Y-%m-%d"
    )

    date_to = datetime.strptime(
        args.date_to,
        "%Y-%m-%d"
    )

    logger.info(
        "Starting Web Crawl..."
    )

    try:

        papers = crawl_arxiv(
            query=args.query,
            date_from=date_from,
            date_to=date_to
        )

    except CrawlError as e:

        # Exit non-zero so the pipeline treats this as a retryable failure
        # instead of proceeding with a missing crawl file.
        logger.error(
            f"Crawl failed and produced nothing: {e}"
        )

        sys.exit(1)

    if len(papers) == 0:

        logger.error(
            "No papers collected; exiting non-zero so the run is retried."
        )

        sys.exit(1)

    save_result(
        papers,
        args.query,
        date_from,
        date_to
    )

    logger.info(
        "Finished."
    )


if __name__ == "__main__":
    main()
