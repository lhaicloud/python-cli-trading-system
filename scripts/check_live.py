import sys, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
path = r'd:\AI Projects\python-cli-trading-system\live_paper_may25.log'
with open(path, encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
print('Total lines:', len(lines))
tagged = [l.rstrip() for l in lines if l.startswith('[')]
pat = re.compile(r'Price:|Paper trade|Cycle #|tp_hit|sl_hit|Signal:|trade opened|trade closed')
key = [l for l in tagged if pat.search(l)]
for l in key[-40:]:
    print(l)
