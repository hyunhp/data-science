"""Fill the `llm` column of a selected-papers CSV with LinkedIn-ready one-liners.

input  : {query_safe}/selected/{input_file}.csv  (needs title + abstract)
output : same file, with an `llm` column of "{title} – {summary}" strings

The summaries are written by the local, already-authenticated Claude Code CLI
(`claude -p`), so the scheduled job needs no separate API key. All papers go in
a single call and come back as JSON keyed by arxiv_id, which keeps parsing robust
and the wording consistent across the issue.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import pandas as pd

# en-dash between title and summary, matching the newsletter format
SEP = ' – '
MAX_RETRY = 3

# Distinct exit code so the orchestrator can tell "the LLM is unavailable"
# (notify + stop at the pre-LLM stage) apart from a genuine failure (retry).
EXIT_LLM_UNAVAILABLE = 3

# stderr fragments that mean claude ran but cannot serve us (login/quota), as
# opposed to a bug in our prompt or a transient parse error
_UNAVAILABLE_HINTS = (
    'not logged in', 'please log in', 'unauthorized', 'authentication',
    'invalid api key', 'no credit', 'credit balance', 'quota', 'rate limit',
    'subscription', 'expired',
)


class LLMUnavailable(Exception):
    """Raised when the LLM cannot be reached at all (missing, not logged in,
    out of quota, or explicitly disabled) — a state that retrying won't fix."""


def find_claude():
    """Absolute path to the claude CLI, or None.

    Task Scheduler does not inherit the interactive shell's PATH, and the claude
    installer drops the binary in ~/.local/bin without adding it to the registry
    PATH. Relying on PATH alone therefore fails under the scheduler even though
    it works in a terminal, so we also probe the known install locations. Set
    CLAUDE_BIN to override.
    """
    override = os.environ.get('CLAUDE_BIN', '').strip()
    if override and os.path.isfile(override):
        return override

    for name in ('claude', 'claude.exe', 'claude.cmd', 'claude.bat'):
        found = shutil.which(name)
        if found:
            return found

    home = os.path.expanduser('~')
    candidates = [
        os.path.join(home, '.local', 'bin', 'claude.exe'),
        os.path.join(home, '.local', 'bin', 'claude.cmd'),
        os.path.join(home, '.local', 'bin', 'claude'),
        os.path.join(home, 'AppData', 'Roaming', 'npm', 'claude.cmd'),
        os.path.join(home, 'AppData', 'Local', 'Programs', 'claude', 'claude.exe'),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def llm_available():
    """Cheap up-front check: is the LLM usable right now?

    Returns False if the user has switched it off (USE_LLM=0) or the claude CLI
    cannot be located. Auth/quota problems only surface on the actual call and
    are reported there as LLMUnavailable.
    """
    if os.environ.get('USE_LLM', '').strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    return find_claude() is not None

PROMPT_TEMPLATE = """You are the editor of a weekly research newsletter on {topic} that is \
posted to LinkedIn for a professional audience. For each paper below, write one \
crisp summary of its abstract.

Rules for every summary:
- Start with a strong present-tense verb (Introduces, Proposes, Shows, Proves, \
Benchmarks, Reframes, Cuts...). Never start with "This paper", "The authors", \
"We", or the paper title.
- 1 to 2 sentences, roughly 30-55 words. Dense and specific: name the method and \
the concrete result or number when the abstract gives one.
- Plain professional English. No hype words ("revolutionary", "groundbreaking"), \
no markdown, no emojis, no citations.
- Do not repeat the title inside the summary.

Return ONLY a JSON array, no prose and no code fences. One object per paper, in \
the same order, each exactly:
{{"arxiv_id": "<id>", "summary": "<your summary sentence(s)>"}}

Papers:
{papers_json}
"""


def build_prompt(topic, df):
    papers = [
        {'arxiv_id': str(row['arxiv_id']),
         'title': str(row['title']),
         'abstract': str(row['abstract'])}
        for _, row in df.iterrows()
    ]
    return PROMPT_TEMPLATE.format(
        topic=topic,
        papers_json=json.dumps(papers, ensure_ascii=False, indent=2),
    )


def extract_json_array(text):
    """Pull the first top-level JSON array out of the model output."""
    # tolerate ```json ... ``` fences the model may add despite instructions
    fenced = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find('[')
        end = text.rfind(']')
        if start == -1 or end == -1 or end < start:
            raise ValueError('No JSON array found in model output')
        raw = text[start:end + 1]
    return json.loads(raw)


def call_claude(prompt):
    """Run `claude -p`, feeding the prompt on stdin. Returns stdout text.

    Raises LLMUnavailable when claude is missing or reports an auth/quota
    problem, and RuntimeError for anything else (a retryable failure).
    """
    claude = find_claude()
    if not claude:
        raise LLMUnavailable('claude CLI not found on PATH or in known locations')

    # Resolve by absolute path (PATH-independent). .cmd/.bat shims need a shell
    # to launch on Windows; a real .exe does not.
    if claude.lower().endswith(('.cmd', '.bat')):
        cmd = ['cmd', '/c', claude, '-p']
    else:
        cmd = [claude, '-p']

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
        )
    except FileNotFoundError as e:
        raise LLMUnavailable(f'claude CLI not found: {e}')

    if result.returncode != 0:
        stderr = (result.stderr or '').strip()
        stdout = (result.stdout or '').strip()
        # Some fatal errors (e.g. an expired OAuth session) are printed to
        # stdout instead of stderr, so check and surface both.
        detail = '\n'.join(part for part in (stderr, stdout) if part) or '(no output)'
        if any(h in detail.lower() for h in _UNAVAILABLE_HINTS):
            raise LLMUnavailable(f'claude -p unavailable: {detail}')
        raise RuntimeError(f'claude -p failed ({result.returncode}): {detail}')
    return result.stdout


def summarize(df, topic):
    """Return df with an `llm` column filled for every row."""
    prompt = build_prompt(topic, df)

    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            output = call_claude(prompt)
            items = extract_json_array(output)
            by_id = {str(it['arxiv_id']): str(it['summary']).strip() for it in items}

            missing = [str(r['arxiv_id']) for _, r in df.iterrows()
                       if str(r['arxiv_id']) not in by_id]
            if missing:
                raise ValueError(f'Model skipped {len(missing)} paper(s): {missing[:3]}...')

            df = df.copy()
            df['llm'] = [
                f"{str(row['title']).strip()}{SEP}{by_id[str(row['arxiv_id'])]}"
                for _, row in df.iterrows()
            ]
            return df
        except LLMUnavailable:
            raise  # retrying cannot fix an unusable LLM — bubble straight up
        except Exception as e:  # noqa: BLE001 - retry any parse/model failure
            last_err = e
            print(f'  summarize attempt {attempt}/{MAX_RETRY} failed: {e}', flush=True)

    raise RuntimeError(f'Summarization failed after {MAX_RETRY} attempts: {last_err}')


def main():
    parser = argparse.ArgumentParser(description='Summarize abstracts into the llm column via claude -p')
    parser.add_argument('--query', type=str, default='Quantum Computing', help='LLM | Quantum Computing')
    parser.add_argument('--input-file', type=str, required=True, help='CSV name under {query_safe}/selected')
    parser.add_argument('--force', action='store_true', help='Re-summarize even if the llm column is already filled')
    args = parser.parse_args()

    query_safe = args.query.replace(' ', '_')
    path = f'{query_safe}/selected/{args.input_file}'
    if not os.path.exists(path):
        print(f'Input not found: {path}', flush=True)
        sys.exit(1)

    df = pd.read_csv(path, dtype={'arxiv_id': str}, encoding='utf-8-sig')

    if 'title' not in df.columns or 'abstract' not in df.columns:
        print("Input must contain 'title' and 'abstract' columns.", flush=True)
        sys.exit(1)

    # Idempotent: skip if already summarized, so hourly retries do not re-spend
    already = 'llm' in df.columns and df['llm'].notna().all() and (df['llm'].astype(str).str.len() > 0).all()
    if already and not args.force:
        print(f'llm column already complete for {args.input_file}, skipping....', flush=True)
        return

    # Off switch / not installed: report the distinct exit code and change nothing
    if not llm_available():
        reason = 'USE_LLM is disabled' if os.environ.get('USE_LLM') else 'claude CLI not found'
        print(f'LLM unavailable ({reason}); leaving pre-LLM output as is.', flush=True)
        sys.exit(EXIT_LLM_UNAVAILABLE)

    try:
        df = summarize(df, args.query)
    except LLMUnavailable as e:
        print(f'LLM unavailable: {e}', flush=True)
        sys.exit(EXIT_LLM_UNAVAILABLE)

    df.to_csv(path, index=False, encoding='utf-8-sig')
    print(f"Summaries written to '{path}' ({len(df)} papers)....", flush=True)


if __name__ == '__main__':
    main()
