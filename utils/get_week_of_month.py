import calendar
from datetime import date, datetime  

def get_week_of_month(date:datetime) -> int:
    cal_month = calendar.monthcalendar(date.year, date.month)
    for week_number, week in enumerate(cal_month, start=1):
        if date.day in week:
            return week_number
    return None

def ordinal_number(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return str(n) + suffix