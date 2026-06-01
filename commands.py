import subprocess
import argparse
from datetime import datetime

parser = argparse.ArgumentParser(description="Crawling data and select the relavant papers")
parser.add_argument('--query', type=str, default='Quantum Computing', help='Search query (Quantum Computing | LLM)')
parser.add_argument('--date-from', type=str, help='Start date (YYYY-MM-DD)')
parser.add_argument('--date-to', type=str, help='End date (YYYY-MM-DD)')
parser.add_argument('--num-papers', type=int, default=15, help='Number of papers to be selected, will be devided by 5 (weekdays) (Default = 15)')
args = parser.parse_args()

UTILS_DIR = "./utils"

# Run the crawling command
subprocess.run(["python", f"{UTILS_DIR}/arxiv_web_crawler.py", "--query", args.query, "--date-from", args.date_from, "--date-to", args.date_to])

# Once the crawling command finishes, run to select the selected papers
query_safe = args.query.replace(' ', '_')
date_from = datetime.strptime(args.date_from, '%Y-%m-%d').strftime('%Y%m%d')
date_to = datetime.strptime(args.date_to, '%Y-%m-%d').strftime('%Y%m%d')
num_papers = str(args.num_papers)
input_file = f'{date_from}_{date_to}.csv'

subprocess.run(["python", f"{UTILS_DIR}/rank_parser.py", "--query", args.query, '--input-file', input_file, '--num-papers', num_papers])