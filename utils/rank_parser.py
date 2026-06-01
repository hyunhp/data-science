import itertools
import pandas as pd
import argparse
import os
from datetime import datetime

def rank_papers(df, query):
    # Define keywords
    query = query.lower()
    if query == 'llm':
        keywords = keywords = [
    "llm",
    "large language model",
    "gpt",
    "reasoning",
    "agent",
    "rag",
    "multimodal",
    "vision language model",
    "alignment",
    "rlhf"
]
    elif query == 'quantum computing':
        keywords = ['quantum algorithm', 'quantum computing', 'quantum machine learning', 'quantum neural network']
    else:
        raise ValueError(f'Input {query} should be LLM or Quantum Computing....')
    
    # Define a function to calculate the score for a paper
    def calculate_score(title, abstract):
        score = 0
        for keyword in keywords:
            if keyword in title.lower():
                score += 1
            if keyword in abstract.lower():
                score += 1
        return score
    
    # Apply the scoring function to each row
    df['score'] = df.apply(lambda row: calculate_score(row['title'], row['abstract']), axis=1)
    
    # Sort the DataFrame by score in descending order
    df_sorted = df.sort_values(by='score', ascending=False).reset_index(drop=True)
    
    return df_sorted

def select_weighted_random_papers(df, num_papers):
    # Group papers by score and ensure at least one paper per score
    groups = df.groupby('score')
    
    selected_papers = []
    remaining_papers = []

    # Ensure at least one paper per score level
    for score in sorted(groups.groups.keys()):
        if score >= 1:
            group_df = groups.get_group(score)
            if len(group_df) > 0:
                # Select one paper from this score group
                selected_papers.append(group_df.sample(n=1))
                remaining_papers.extend(group_df.index.tolist())
    
    # Flatten the list of DataFrames into a single DataFrame
    selected_papers_df = pd.concat(selected_papers)

    # Exclude selected papers from remaining papers
    remaining_papers = list(set(remaining_papers) - set(selected_papers_df.index.tolist()))

    # Randomly select from the remaining papers weighted by score
    remaining_df = df.loc[remaining_papers]
    remaining_df['weight'] = remaining_df['score']  # Use score as weight
    weighted_papers = remaining_df.sample(n=int(num_papers) - len(selected_papers_df), weights='weight', replace=False)

    # Combine selected papers and weighted papers
    final_selection = pd.concat([selected_papers_df, weighted_papers])
    final_selection.drop(columns=['weight'], inplace=True)

    # Concat information for LLM Prompting
    prompt = '''Output Guide
1. {summarized about abstract} : Please summarizes the abstract to explain the paper
2. Final format should be {title} - {summarized about abstract}
3. No need to start with 'this paper', just starts with verb."'''
    
    final_selection['concat'] = final_selection.apply(lambda x : "<INPUT>\nTitle : " + x['title'] + '\nAbstract : ' + x['abstract'] + f'</INPUT>\n\n{prompt}', axis=1)

    # Concat dates for newsletter
    dates =['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    papers_per_dates = int(num_papers / len(dates))
    final_selection['date'] = list(itertools.chain(*[[date]*papers_per_dates for date in dates]))
    
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
    df = pd.read_csv(f'{query_safe}/crawling/{args.input_file}')
    
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
