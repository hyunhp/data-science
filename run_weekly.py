"""Weekly newsletter pipeline — the single entry point for the scheduled job.

For every topic it runs the whole chain end to end:

    crawl  ->  rank/select  ->  summarize (claude -p)  ->  LinkedIn-ready .txt

Defaults, with no arguments:
  * period       = last week, Monday to Sunday
  * upload date  = the upcoming Monday

Two failure modes are handled differently:

  * A real error in any stage (crawl/rank/newsletter, or a broken LLM response)
    exits non-zero, which lets the Task Scheduler "restart every hour" retry it
    until the output is produced.
  * The LLM being *unavailable* (claude not installed, not logged in, out of
    quota, or turned off via USE_LLM=0) is NOT retried: the run still produces
    everything up to the pre-LLM stage (the selected/ CSV) and fires a desktop
    notification so you can summarize it yourself or turn the LLM back on.

It is idempotent: a topic whose newsletter for the target week already exists is
skipped, and the crawl/rank stages are skipped if their output is already there,
so repeated runs only fill in what is still missing.
"""
import glob
import os
import subprocess
import sys
from datetime import datetime, timedelta

# Make our own console output utf-8 so en-dashes etc. never crash on cp949
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:  # noqa: BLE001
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
UTILS = os.path.join(ROOT, 'utils')
LOG_DIR = os.path.join(ROOT, 'logs')
sys.path.insert(0, UTILS)
from summarize_abstract import EXIT_LLM_UNAVAILABLE  # noqa: E402

QUERIES = ['LLM', 'Quantum Computing']
NUM_RANKED = 15   # papers carried into the selected/ file
NUM_POSTED = 5    # papers that make the LinkedIn post

# utf-8 for every child so summaries with en-dashes never break a piped stdout.
# Also put ~/.local/bin on PATH: Task Scheduler starts us without it, and that
# is where the claude CLI lives.
_LOCAL_BIN = os.path.join(os.path.expanduser('~'), '.local', 'bin')
CHILD_ENV = {
    **os.environ,
    'PYTHONIOENCODING': 'utf-8',
    'PYTHONUTF8': '1',
    'PATH': _LOCAL_BIN + os.pathsep + os.environ.get('PATH', ''),
}


def last_week_range(today):
    """Monday ~ Sunday of the week before the one containing `today`."""
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def upcoming_monday(today):
    """The next Monday strictly after today (7 days out if today is Monday)."""
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


class Logger:
    """Write every line to both the console and the day's log file."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fh = open(path, 'a', encoding='utf-8')

    def __call__(self, msg=''):
        stamp = datetime.now().strftime('%H:%M:%S')
        line = f'[{stamp}] {msg}' if msg else ''
        print(line, flush=True)
        self.fh.write(line + '\n')
        self.fh.flush()

    def close(self):
        self.fh.close()


def notify(log, title, message):
    """Best-effort desktop toast + a durable marker file + a log line.

    The marker file and log always work headless; the balloon only shows when a
    desktop session is present, which is fine as a bonus.
    """
    log(f'  ** NOTIFY: {title} — {message}')
    try:
        marker = os.path.join(LOG_DIR, f'NEEDS_ATTENTION_{datetime.now():%Y%m%d}.txt')
        with open(marker, 'a', encoding='utf-8') as fh:
            fh.write(f'[{datetime.now():%Y-%m-%d %H:%M}] {title}: {message}\n')
    except Exception as e:  # noqa: BLE001
        log(f'     (marker file failed: {e})')

    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Warning;$n.Visible=$true;"
        f"$n.ShowBalloonTip(10000,'{title}','{message}',"
        "[System.Windows.Forms.ToolTipIcon]::Warning);Start-Sleep -Seconds 6;$n.Dispose()"
    )
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                       timeout=20, capture_output=True)
    except Exception as e:  # noqa: BLE001 - notification is optional
        log(f'     (toast skipped: {e})')


def run_step(log, name, cmd, allow=()):
    """Run one sub-script, log its output, return its exit code.

    Raises RuntimeError on a non-zero code unless it is listed in `allow`.
    """
    log(f'  -> {name}')
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=CHILD_ENV)
    for stream in (result.stdout, result.stderr):
        for raw in (stream or '').splitlines():
            if raw.strip():
                log(f'     | {raw}')
    if result.returncode != 0 and result.returncode not in allow:
        raise RuntimeError(f'{name} exited with code {result.returncode}')
    return result.returncode


def find_output(query_safe, subdir, df, dt, prefix_glob='*'):
    """First existing file for this week's date range, or None."""
    pattern = os.path.join(ROOT, query_safe, subdir,
                           f'{prefix_glob}({df}_{dt}).*')
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else None


def process_query(log, query, date_from, date_to, planned_date, today_str):
    """Returns 'done', 'skipped', or 'no-llm'. Raises on a real error."""
    query_safe = query.replace(' ', '_')
    df, dt = date_from.strftime('%Y%m%d'), date_to.strftime('%Y%m%d')
    py = sys.executable

    log(f'=== {query} ===')
    if find_output(query_safe, 'newsletter', df, dt):
        log(f'  newsletter for {df}_{dt} already exists, skipping.')
        return 'skipped'

    crawl_file = f'{df}_{dt}.csv'
    selected_file = f'{today_str}_newsletter({df}_{dt}).csv'

    # Pre-LLM stages — skip if a selected/ file for this week is already there
    existing_selected = find_output(query_safe, 'selected', df, dt)
    if existing_selected:
        selected_file = os.path.basename(existing_selected)
        log(f'  reusing existing selection {selected_file}')
    else:
        run_step(log, 'crawl', [py, os.path.join(UTILS, 'arxiv_web_crawler.py'),
                                '--query', query, '--date-from', date_from.strftime('%Y-%m-%d'),
                                '--date-to', date_to.strftime('%Y-%m-%d')])
        run_step(log, 'rank/select', [py, os.path.join(UTILS, 'rank_parser.py'),
                                      '--query', query, '--input-file', crawl_file,
                                      '--num-papers', str(NUM_RANKED)])

    # LLM stage — a special exit code means "unavailable", handled softly
    code = run_step(log, 'summarize',
                    [py, os.path.join(UTILS, 'summarize_abstract.py'),
                     '--query', query, '--input-file', selected_file],
                    allow=(EXIT_LLM_UNAVAILABLE,))
    if code == EXIT_LLM_UNAVAILABLE:
        notify(log, 'Newsletter: LLM unavailable',
               f'{query} stopped before summaries. Selection is ready at '
               f'{query_safe}/selected/{selected_file}.')
        return 'no-llm'

    run_step(log, 'newsletter', [py, os.path.join(ROOT, 'weekly_newsletter.py'),
                                 '--query', query, '--input-file', selected_file,
                                 '--num-papers', str(NUM_POSTED),
                                 '--planned-date', planned_date.strftime('%Y-%m-%d')])
    out = selected_file.replace('.csv', '.txt')
    log(f'  {query} done: {query_safe}/newsletter/weekly_{out}')
    return 'done'


def main():
    today = datetime.today()
    today_str = today.strftime('%Y%m%d')
    date_from, date_to = last_week_range(today)
    planned_date = upcoming_monday(today)

    log = Logger(os.path.join(LOG_DIR, f'weekly_{today_str}.log'))
    log('================ weekly run start ================')
    log(f'period {date_from:%Y-%m-%d} ~ {date_to:%Y-%m-%d} | upload {planned_date:%Y-%m-%d}')

    failures, no_llm = [], []
    for query in QUERIES:
        try:
            status = process_query(log, query, date_from, date_to, planned_date, today_str)
            if status == 'no-llm':
                no_llm.append(query)
        except Exception as e:  # noqa: BLE001 - one topic failing must not sink the other
            failures.append(query)
            log(f'  !! {query} FAILED: {e}')

    if no_llm:
        log(f'pre-LLM only (notified): {no_llm}')
    if failures:
        # Real errors -> non-zero exit -> Task Scheduler retries in an hour
        log(f'run finished with failures: {failures}')
        log('==================================================')
        log.close()
        sys.exit(1)

    log('all topics complete.' if not no_llm else 'done (some topics pre-LLM only).')
    log('==================================================')
    log.close()


if __name__ == '__main__':
    main()
