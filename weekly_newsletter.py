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
    df = df.sample(n=num_papers, random_state=42, replace=False).reset_index(drop=True)

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
    