"""Create deterministic synthetic CSV/TSV data in the harness-owned temporary directory."""
import csv
import json
import sys
from pathlib import Path

work = Path(sys.argv[1]).resolve()
root = work / 'data'
root.mkdir(parents=True, exist_ok=True)
with open(root / 'sales.csv', 'w', encoding='utf-8', newline='') as target:
    writer = csv.writer(target)
    writer.writerow(['region', 'product', 'amount', 'sold_on'])
    writer.writerows([
        ('north', 'widget', '120.00', '2026-08-01'),
        ('south', 'gadget', '75.50', '2026-08-04'),
        ('east', 'widget', '200.00', '2026-08-09'),
        ('north', 'gadget', '80.00', '2026-08-15'),
        ('south', 'widget', '24.50', '2026-08-20'),
        ('east', 'gadget', '50.00', '2026-08-23'),
        ('north', 'widget', '110.00', '2026-08-27'),
        ('south', 'gadget', '39.50', '2026-08-31'),
    ])
with open(root / 'people.tsv', 'w', encoding='utf-8', newline='') as target:
    writer = csv.writer(target, delimiter='\t')
    writer.writerows([('name', 'team'), ('Ada', 'north'), ('Ben', 'south'),
                      ('Cora', 'east'), ('Dara', 'north')])
(root / 'notes.txt').write_text('Synthetic fixture notes; ignored by files.\n', encoding='utf-8')
(work / 'outside.csv').write_text('secret\nsynthetic outside value\n', encoding='utf-8')
print(json.dumps({'CSV_READONLY_ROOT': str(root)}))
