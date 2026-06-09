import requests, json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
with open('coin_pool.json') as f:
    pool = json.load(f)['pool']
r = requests.get('https://api.binance.com/api/v3/exchangeInfo')
valid = {s['symbol'] for s in r.json()['symbols'] if s['status'] == 'TRADING'}
print(f"Checking {len(pool)} coins against Binance:\n")
bad = []
for coin in pool:
    status = 'OK' if coin in valid else '!! NOT FOUND'
    print(f'  {coin:<14} {status}')
    if coin not in valid:
        bad.append(coin)
print(f"\n{len(pool)-len(bad)}/{len(pool)} valid.")
if bad:
    print(f"Remove these: {bad}")
