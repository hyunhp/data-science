import itertools
import random
import re
import sys
import pandas as pd
import argparse
import os
from datetime import datetime

# Keywords as (topic, regex, weight).
#
# Patterns match on word boundaries, so 'rag' no longer scores on 'average',
# 'coverage' or 'storage', while plurals ('LLMs') and derivations ('agentic')
# still count.
#
# Weights favour the specific sub-topics: a term that nearly every crawled
# paper contains ('llm') separates papers far less than a rare one ('rlhf').
#
# The topic is what keeps the newsletter varied, see select_weighted_random_papers.
KEYWORDS = {
    'llm': [
        ('core',           r'\bllms?\b',                                    1),
        ('core',           r'\blarge language models?\b',                   1),
        ('core',           r'\bgpt[\w-]*\b',                                1),
        ('reasoning',      r'\breasoning\b',                                2),
        ('reasoning',      r'\b(?:chain[- ]of[- ]thought|cots?)\b',         2),
        ('agent',          r'\b(?:agents?|agentic)\b',                      2),
        ('rag',            r'\b(?:rags?|retrieval[- ]augmented)\b',         3),
        ('multimodal',     r'\bmulti[- ]?modal\w*\b',                       3),
        ('multimodal',     r'\bvision[- ]language models?\b',               3),
        ('multimodal',     r'\bvlms?\b',                                    3),
        ('alignment',      r'\balignments?\b',                              3),
        ('alignment',      r'\brlhf\b',                                     3),
        ('efficiency',     r'\b(?:distillation|distill(?:ed|ing)?)\b',      3),
        ('efficiency',     r'\b(?:quantization|quantized|pruning)\b',       3),
        ('hallucination',  r'\bhallucinat\w*\b',                            3),
    ],
    'quantum computing': [
        ('algorithm',      r'\bquantum algorithms?\b',                      2),
        ('computing',      r'\bquantum comput\w*\b',                        1),
        ('qml',            r'\bquantum machine learning\b',                 3),
        ('qml',            r'\bquantum neural networks?\b',                 3),
        ('error-correct',  r'\bquantum error correction\b',                 3),
        ('error-correct',  r'\bqecc?\b',                                    3),
        ('hardware',       r'\bqubits?\b',                                  1),
        ('variational',    r'\bvariational quantum\w*\b',                   3),
        ('variational',    r'\b(?:vqe|qaoa)\b',                             3),
        ('advantage',      r'\bquantum (?:advantage|supremacy)\b',          3),
    ],
}

# A keyword in the title is a stronger signal than one buried in the abstract
TITLE_BOOST = 2

def rank_papers(df, query):
    query = query.lower()
    if query not in KEYWORDS:
        raise ValueError(f'Input {query} should be LLM or Quantum Computing....')

    patterns = [
        (topic, re.compile(pattern), weight)
        for topic, pattern, weight in KEYWORDS[query]
    ]

    # Define a function to calculate the score for a paper
    def calculate_score(title, abstract):
        title, abstract = str(title).lower(), str(abstract).lower()
        score, topics = 0, set()
        for topic, pattern, weight in patterns:
            if pattern.search(title):
                score += weight * TITLE_BOOST
                topics.add(topic)
            if pattern.search(abstract):
                score += weight
                topics.add(topic)
        return score, ','.join(sorted(topics))

    # Apply the scoring function to each row
    df[['score', 'topics']] = df.apply(
        lambda row: calculate_score(row['title'], row['abstract']), axis=1, result_type='expand')

    # Sort the DataFrame by score in descending order
    df_sorted = df.sort_values(by='score', ascending=False).reset_index(drop=True)

    return df_sorted

def select_weighted_random_papers(df, num_papers):
    num_papers = int(num_papers)

    # Only papers that matched at least one keyword are eligible
    candidates = df[df['score'] >= 1]

    if len(candidates) == 0:
        raise ValueError('No paper matched any keyword, nothing to select....')

    # Never ask for more papers than we actually crawled
    if len(candidates) < num_papers:
        print(f'Only {len(candidates)} scored papers available, requested {num_papers}. Selecting {len(candidates)}....')
        num_papers = len(candidates)

    # Round-robin over the topics, taking one paper per topic per pass, so a
    # single hot sub-topic cannot fill the whole issue. Within a topic the pick
    # is still weighted random by score, which keeps week-to-week variety.
    candidates = candidates.copy()
    topic_sets = candidates['topics'].apply(lambda t: set(str(t).split(',')))

    remaining = candidates.index.tolist()
    picked = []

    while len(picked) < num_papers and remaining:
        topics = sorted({t for idx in remaining for t in topic_sets[idx]})
        # A topic every candidate shares ('core' on the LLM query) says nothing
        # about how the papers differ, so it must not consume a slot
        discriminating = [t for t in topics
                          if sum(1 for idx in remaining if t in topic_sets[idx]) < len(remaining)]
        topics = discriminating or topics
        random.shuffle(topics)

        for topic in topics:
            if len(picked) >= num_papers:
                break
            pool = [idx for idx in remaining if topic in topic_sets[idx]]
            if not pool:
                continue
            pick = candidates.loc[pool].sample(n=1, weights='score').index[0]
            picked.append(pick)
            remaining.remove(pick)

    final_selection = candidates.loc[picked]

    # Concat information for LLM Prompting
    prompt = '''Output Guide
1. {summarized about abstract} : Please summarizes the abstract to explain the paper
2. Final format should be {title} - {summarized about abstract}
3. No need to start with 'this paper', just starts with verb."'''
    
    final_selection['concat'] = final_selection.apply(lambda x : "<INPUT>\nTitle : " + x['title'] + '\nAbstract : ' + x['abstract'] + f'</INPUT>\n\n{prompt}', axis=1)

    # Concat dates for newsletter, spreading the papers evenly over the weekdays
    dates =['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    total = len(final_selection)
    papers_per_dates = [total // len(dates) + (1 if i < total % len(dates) else 0) for i in range(len(dates))]
    final_selection['date'] = list(itertools.chain(*[[date]*count for date, count in zip(dates, papers_per_dates)]))

    return final_selection

def main():
    parser = argparse.ArgumentParser(description="Rank and select arXiv papers based on keywords and score.")
    parser.add_argument('--query', type=str, default='Quantum Computing', help='Search query (Default : Quantum Computing)')
    parser.add_argument('--input-file', type=str, required=True, help='Input CSV file with papers data')
    parser.add_argument('--num-papers', type=int, default=15, help='Number of papers to select')
       
    args = parser.parse_args()
    
    # Save the selected papers to a new CSV file
    query_safe = args.query.replace(' ', '_')
    
    # Read the input CSV file
    # arxiv_id must stay a string, pandas otherwise reads it as a float and
    # drops the trailing digit of ids like 2607.11410
    crawl_path = f'{query_safe}/crawling/{args.input_file}'
    if not os.path.exists(crawl_path):
        print(f'Crawl file not found: {crawl_path}. Did the crawl step succeed?')
        sys.exit(1)
    df = pd.read_csv(crawl_path, dtype={'arxiv_id': str})
    
    # Check for required columns
    if 'title' not in df.columns or 'abstract' not in df.columns:
        print("The input file must contain 'title' and 'abstract' columns.")
        return
    
    # Rank the papers
    df_ranked = rank_papers(df, args.query)
    
    # Select random papers weighted by score
    df_selected = select_weighted_random_papers(df_ranked, args.num_papers)
    
    if not os.path.exists(f'{query_safe}/selected'):
        os.makedirs(f'{query_safe}/selected')
    today = datetime.today().strftime('%Y%m%d')
    date_range = args.input_file.replace('.csv', '')
    file_path = f'{query_safe}/selected/{today}_newsletter({date_range}).csv'
    
    if os.path.exists(file_path):
        os.remove(file_path)
    df_selected.to_csv(file_path, index=False, encoding='utf-8-sig', mode='w+')
    print(f"Selected papers '{today}_newsletter({date_range}).csv' saved....")

if __name__ == '__main__':
    main()
