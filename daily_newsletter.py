import pandas as pd
import argparse
import os
import re
from datetime import datetime

parser = argparse.ArgumentParser(description="Convert into the Newsletter format")
parser.add_argument('--query', type=str,  default='Quantum Computing', help='Search query (Default : Quantum Computing)')
parser.add_argument('--input-file', type=str, required=True, help='Input File under the selected folder')
args = parser.parse_args()

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
        
        print(f"Start Date: {start_date_formatted}")
        print(f"End Date: {end_date_formatted}")
    else:
        print("No dates found in the filename.")
    return start_date_formatted, end_date_formatted

# Function to convert date in YYYYMMDD format to readable string format
def format_date(date_str):
    date_obj = datetime.strptime(date_str, '%Y%m%d')
    return date_obj.strftime('%d %B')

def set_information(query, input_file):
    if query == 'quantum computing':
        start_date_formatted, end_date_formatted = convert_date(input_file)
        
        input_path = './Quantum_Computing/selected'
        output_path = './Quantum_Computing/newsletter'        
        header = f'''📰 [Date] in Quantum_Computing: Today's Cutting-Edge Papers
Top Quantum Computing Papers ({start_date_formatted} - {end_date_formatted})

Dive into the most compelling and innovative research in the field of quantum computing. This week’s selections highlight cutting-edge advances and theoretical developments.'''
        tail = '''Thank you for joining us for this week’s Quantum Computing Highlights!
Trust you found these papers to be a valuable addition to your quantum research journey. Keep an eye out for the next newsletter, will bring you more of the latest breakthroughs and discussions in quantum computing.

Welcome any comments or suggestions you might have. Don’t hesitate to get in touch with!

Best regards,
Hyunho'''
        introduction = '''Stay Ahead in Quantum Computing—Discover Breakthroughs Now! 

[Research Paper List]#QuantumInsights #QuantumComputing #Research #Study'''

    elif query == 'llm':
        start_date_formatted, end_date_formatted = convert_date(input_file)

        input_path  = './LLM/selected'
        output_path = './LLM/newsletter'
        header = f'''📰 LLM Research Roundup: [Date] Highlights
The Top LLM Papers ({start_date_formatted} - {end_date_formatted})

Explore the latest and most intriguing research papers in the world of Large Language Models. Whether you’re a researcher, enthusiast, or just curious, these papers offer fresh insights and developments in the field.'''
        tail = '''That’s a wrap for this week’s edition of LLM Insights!
Hope you found these papers as fascinating and insightful. Stay tuned for next week’s roundup of the latest advancements in Large Language Models. Until then, happy reading and exploring the world of LLMs!

If you have any feedback or suggestions for future editions, feel free to reach out to me.

Best regards,
Hyunho'''
        introduction  ='''Be in the Know: The Future of Artificial Intelligence is Here! 

[Research Paper List]#LLMUpdates #GenAI #Research #Study'''

    else :
        raise ValueError(f'Query should be Quantum Computing or LLM, not {args.query}')
    return input_path, output_path, header, tail, introduction

def main():
    input_path, output_path, header, tail, introduction = set_information(args.query, args.input_file)
    df = pd.read_csv(input_path + '/' + args.input_file, encoding='utf-8-sig')
    text = ''
    dates =['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

    for date in dates:    
        text += header.replace('[Date]', date)
        count, research_papers = 1, ''
        for _, row in df.iterrows():
            if row['date'] == date:
                try : 
                    title, content  = row["llm"].split(" – ")[0], row["llm"].split(" – ")[1]
                except :
                    title, content  = row["llm"].split(" - ")[0], row["llm"].split(" - ")[1]
                text += f'\n\n**({count}) {title}** – {content}\n\nRead More : {row["url"]}'
                research_papers += f'({count}) {title}\n\n'
                count += 1
        research_papers = introduction.replace('[Research Paper List]', research_papers)
        text += '\n\n' + tail + '\n-----------------------------------\n' + research_papers + '\n===================================\n'

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    output_file = args.input_file.replace('.csv', '.txt')

    with open(f'{output_path}/{output_file}', 'w', encoding='utf-8-sig') as f:
        f.write(text)

    today = datetime.today().strftime('%Y/%m/%d')
    print(f'{today} neswletter created....')

if __name__ == "__main__":
    main()
    