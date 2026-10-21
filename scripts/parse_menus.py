import json
from pathlib import Path

raw_file = Path('output/master_screens/menus_raw.txt')
lines = raw_file.read_text(encoding='latin1').splitlines()

menus = {}
for line in lines:
    line = line.strip()
    if not line or '^' not in line:
        continue
    parts = line.split('^')
    if len(parts) >= 5:
        m_id = int(parts[0].strip())
        m_name = parts[1].strip()
        m_parent = int(parts[2].strip())
        m_shortcut = parts[3].strip()
        m_form = parts[4].strip()
        if m_name and m_name != '-':
            menus[m_id] = {
                'id': m_id,
                'name': m_name,
                'parent_id': m_parent,
                'shortcut': m_shortcut if m_shortcut else None,
                'form': m_form if m_form and m_form != 'xx' else None
            }

for m_id, m in menus.items():
    path = [m['name']]
    curr = m['parent_id']
    visited = set()
    while curr in menus and curr != 0 and curr != -1 and curr not in visited:
        visited.add(curr)
        path.insert(0, menus[curr]['name'])
        curr = menus[curr]['parent_id']
    m['full_path'] = ' > '.join(path)

print(f"Total de menus/telas oficiais no Master: {len(menus)}")

out_json = Path('output/master_screens/catalogo_oficial_telas_master.json')
with open(out_json, 'w', encoding='utf-8') as f:
    json.dump(list(menus.values()), f, ensure_ascii=False, indent=2)

com_atalho = [m for m in menus.values() if m['shortcut']]
print(f"Total de telas com teclas de atalho: {len(com_atalho)}")
for m in com_atalho[:12]:
    print(f" - {m['full_path']} | Atalho: {m['shortcut']} | Form: {m['form']}")
