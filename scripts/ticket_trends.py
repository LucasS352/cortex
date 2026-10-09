"""Monthly intake analysis from ticket creation dates, separate from the open queue."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
import unicodedata

LOCAL_TZ = ZoneInfo('America/Sao_Paulo')


def bounds(start: date, end: date):
    if end < start or (end - start).days > 366:
        raise ValueError('Selecione até 367 dias, com data final igual ou posterior à inicial.')
    return (datetime.combine(start, datetime.min.time(), LOCAL_TZ),
            datetime.combine(end + timedelta(days=1), datetime.min.time(), LOCAL_TZ))


def previous_dates(start: date, end: date):
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def subject_key(value: str):
    value = re.sub(r'\s+', ' ', value or '').strip()
    return unicodedata.normalize('NFKC', value).casefold() or '(sem assunto)'


def error_reported(value: str):
    plain = ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                    if not unicodedata.combining(c))
    return bool(re.search(r'\b(erro|falha|rejeic\w*|invalido|incompativel|divergente|nao|sem|timeout|exception|instabilidade|fora do ar)\b', plain))


def summarize(rows, start: date, end: date, selected_month: str, topic_fn, previous_count: int):
    monthly = defaultdict(list)
    for row in rows:
        created = row['created_date'].astimezone(LOCAL_TZ)
        month = created.strftime('%Y-%m')
        monthly[month].append(row)
    first_month = date(start.year, start.month, 1)
    last_month = date(end.year, end.month, 1)
    months = []
    cursor = first_month
    while cursor <= last_month:
        key = cursor.strftime('%Y-%m')
        items = monthly[key]
        topics = Counter(topic_fn(r.get('subject') or '', '') for r in items)
        next_month = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        partial = start > cursor or end < next_month - timedelta(days=1)
        months.append({'month': key, 'count': len(items),
                       'top_topic': topics.most_common(1)[0][0] if topics else None,
                       'partial': partial})
        cursor = next_month
    valid_months = {m['month'] for m in months}
    if selected_month not in valid_months:
        selected_month = next((m['month'] for m in reversed(months) if not m['partial']), months[-1]['month'])
    selected = monthly[selected_month]
    groups = defaultdict(list)
    titles = {}
    for row in selected:
        title = re.sub(r'\s+', ' ', row.get('subject') or '').strip() or '(sem assunto)'
        key = subject_key(title)
        groups[key].append(row)
        titles.setdefault(key, title)
    def ranked(only_errors):
        output = []
        candidates = ((key, items) for key, items in groups.items()
                      if not only_errors or error_reported(titles[key]))
        for key, items in sorted(candidates, key=lambda pair: (-len(pair[1]), pair[0]))[:10]:
            newest = sorted(items, key=lambda item: item['created_date'], reverse=True)[:5]
            output.append({'subject': titles[key], 'count': len(items),
                           'examples': [{'id': r['external_id'], 'created_at': r['created_date'].isoformat()}
                                        for r in newest]})
        return output
    topics = Counter(topic_fn(r.get('subject') or '', '') for r in selected)
    return {'from': start.isoformat(), 'to': end.isoformat(),
            'selected_month': selected_month, 'total': len(rows),
            'previous_total': previous_count,
            'monthly': months, 'month_total': len(selected),
            'topics': [{'name': name, 'count': count} for name, count in topics.most_common()],
            'recurring': ranked(False), 'reported_errors': ranked(True),
            'method': 'Chamados agrupados pela data de abertura no horário de São Paulo. Assuntos iguais são agrupados pelo texto do título; isso não confirma causa raiz.'}
