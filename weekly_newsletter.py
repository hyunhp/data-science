import pandas as pd
import argparse
import os
import re
import json
from datetime import datetime
from utils.get_week_of_month import get_week_of_month, ordinal_number

# Set up argument parser
parser = argparse.ArgumentParser(description="Convert into the Newsletter format")
parser.add_argument('--query', type=str,  default='Quantum Computing', help='Search query (Default : Quantum Computing)')
parser.add_argument('--input-file', type=str, required=True, help='Input File under the selected folder')
parser.add_argument('--num-papers', type=int, default=5, help='Number of papers to be selected (Default = 5)')
parser.add_argument('--planned-date', type=str, required=True, help='Upload planned Monday date (YYYY-MM-DD)')

args = parser.parse_args()

# Load template JSON
with open("./newsletter_template.json", "r", encoding="utf-8") as f:
    templates = json.load(f)

# Convert date into readable string format
def convert_date(input_file):    
    # Extract the dates from the filename
    match = re.search(r'\((\d{8})_(\d{8})\)', input_file)

    if match:
        start_date_str = match.group(1)
        end_date_str = match.group(2)
        
        # Format dates
        start_date_formatted = format_date(start_date_str)
        end_date_formatted = format_date(end_date_str)
        
    else:
        print("No dates found in the filename.")
    return start_date_formatted, end_date_formatted

# Function to convert date in YYYYMMDD format to readable string format
def format_date(date_str):
    date_obj = datetime.strptime(date_str, '%Y%m%d')
    return date_obj.strftime('%d %B')


# Pick papers that cover as many different topics as possible, so the LinkedIn
# post does not end up being five papers about the same thing. Falls back to a
# plain random sample for selected files written before the topic column existed.
def pick_diverse(df, num_papers, seed=42):
    if 'topics' not in df.columns:
        return df.sample(n=num_papers, random_state=seed, replace=False).reset_index(drop=True)

    shuffled = df.sample(frac=1, random_state=seed).reset_index(drop=True)
    topic_sets = [set(str(t).split(',')) for t in shuffled['topics']]

    # A topic every paper shares ('core' on the LLM query) tells us nothing
    # about how they differ, and would block every pick after the first
    universal = set.intersection(*topic_sets) if topic_sets else set()
    topic_sets = [topics - universal for topics in topic_sets]

    picked, used_topics = [], set()

    while len(picked) < num_papers:
        progressed = False
        for i in range(len(shuffled)):
            if len(picked) >= num_papers:
                break
            if i in picked:
                continue
            topics = topic_sets[i]
            if topics & used_topics:
                continue
            picked.append(i)
            used_topics |= topics
            progressed = True
        # Every remaining paper overlaps what we already took, start a fresh round
        if not progressed:
            if not used_topics:
                break
            used_topics = set()

    return shuffled.loc[picked].reset_index(drop=True)

def set_information(query, input_file):
    if query == 'Quantum Computing':
        start_date_formatted, end_date_formatted = convert_date(input_file)
        
        input_path = templates['Quantum_Computing']['input_path']
        output_path = templates['Quantum_Computing']['output_path']
        header = templates['Quantum_Computing']['header'] \
            .replace('{start_date_formatted}', start_date_formatted) \
            .replace('{end_date_formatted}', end_date_formatted)
        tail = templates['Quantum_Computing']['tail']
        introduction = templates['Quantum_Computing']['introduction'] 

    elif query == 'LLM':
        start_date_formatted, end_date_formatted = convert_date(input_file)

        input_path = templates['LLM']['input_path']
        output_path = templates['LLM']['output_path']
        header = templates['LLM']['header'] \
            .replace('{start_date_formatted}', start_date_formatted) \
            .replace('{end_date_formatted}', end_date_formatted)
        tail = templates['LLM']['tail']
        introduction = templates['LLM']['introduction'] 

    else :
        raise ValueError(f'Query should be Quantum Computing or LLM, not {args.query}')
    return input_path, output_path, header, tail, introduction

def main():
    # Get paths and templates
    input_path, output_path, header, tail, introduction = set_information(args.query, args.input_file)

    # Get week of month
    upload_date = datetime.strptime(args.planned_date, '%Y-%m-%d') 
    week_of_month = ordinal_number(get_week_of_month(upload_date))

    # Read selected papers
    num_papers = args.num_papers

    df = pd.read_csv(input_path + '/' + args.input_file, encoding='utf-8-sig')

    if len(df) == 0:
        raise ValueError(f'{args.input_file} has no paper to write about....')

    # Never ask for more papers than the selected file actually holds
    if len(df) < num_papers:
        print(f'Only {len(df)} papers available, requested {num_papers}. Using {len(df)}....')
        num_papers = len(df)

    df = pick_diverse(df, num_papers)

    count, research_papers, text = 1, '', header
    text = text.replace('[Week Info]', f'{week_of_month} Week of {upload_date.strftime("%B %Y")}')

    for _, row in df.iterrows():
        try : 
            title, content  = row["llm"].split(" – ")[0], row["llm"].split(" – ")[1]
        except :
            title, content  = row["llm"].split(" - ")[0], row["llm"].split(" - ")[1]
        text += f'\n\n**({count}) {title}** – {content}\n\nRead More : {row["url"]}'
        research_papers += f'({count}) {title}\n'
        count += 1

    research_papers = introduction.replace('[Research Paper List]', research_papers)
    research_papers = research_papers.replace('[Count]', str(count-1))

    text += '\n\n' + tail + '\n-----------------------------------\n' + research_papers + '\n===================================\n'

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    output_file = args.input_file.replace('.csv', '.txt')

    with open(f'{output_path}/weekly_{output_file}', 'w', encoding='utf-8-sig') as f:
        f.write(text)

    today = datetime.today().strftime('%Y/%m/%d')
    print(f'Weekely {args.query} neswletter completed for posting on {args.planned_date}....')

if __name__ == "__main__":
    main()
    