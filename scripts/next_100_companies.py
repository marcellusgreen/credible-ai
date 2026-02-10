#!/usr/bin/env python3
"""Generate prioritized list of next 100 companies to add."""

# Companies already in DB
in_db = set(['AAL', 'AAPL', 'ABBV', 'ABNB', 'ABT', 'ACN', 'ADBE', 'ADI', 'ADSK', 'AEP',
'AMAT', 'AMC', 'AMD', 'AMGN', 'AMZN', 'ANET', 'APA', 'APH', 'APP', 'ATUS',
'AVGO', 'AXON', 'AXP', 'BA', 'BAC', 'BHC', 'BIIB', 'BKNG', 'BKR', 'BLK',
'BSX', 'BX', 'C', 'CAR', 'CAT', 'CB', 'CCL', 'CDNS', 'CDW', 'CEG',
'CHS', 'CHTR', 'CLF', 'CNK', 'COF', 'COP', 'COST', 'CPRT', 'CRM', 'CRWD',
'CRWV', 'CSCO', 'CSGP', 'CSX', 'CTAS', 'CTSH', 'CVNA', 'CVX', 'CZR', 'DAL',
'DASH', 'DDOG', 'DE', 'DHR', 'DIS', 'DISH', 'DO', 'DVN', 'DXCM', 'EA',
'ETN', 'EXC', 'F', 'FANG', 'FAST', 'FOX', 'FTNT', 'FUN', 'FYBR', 'GE',
'GEHC', 'GEV', 'GFS', 'GILD', 'GM', 'GOOGL', 'GS', 'HCA', 'HD', 'HON',
'HSY', 'HTZ', 'IBM', 'IDXX', 'IHRT', 'INTC', 'INTU', 'ISRG', 'JNJ', 'JPM',
'KDP', 'KHC', 'KLAC', 'KO', 'KSS', 'LIN', 'LLY', 'LMT', 'LOW', 'LRCX',
'LULU', 'LUMN', 'LYV', 'M', 'MA', 'MAR', 'MCD', 'MCHP', 'MDLZ', 'MDT',
'META', 'MGM', 'MNST', 'MRK', 'MRVL', 'MS', 'MSFT', 'MSTR', 'MU', 'NCLH',
'NE', 'NEE', 'NEM', 'NFLX', 'NOW', 'NRG', 'NVDA', 'NXPI', 'ODFL', 'ON',
'ORCL', 'ORLY', 'OXY', 'PANW', 'PARA', 'PAYX', 'PCAR', 'PEP', 'PFE', 'PG',
'PGR', 'PH', 'PLD', 'PLTR', 'PM', 'PYPL', 'QCOM', 'RCL', 'REGN', 'RIG',
'ROP', 'ROST', 'RTX', 'SBUX', 'SCHW', 'SLG', 'SNPS', 'SPG', 'SPGI', 'SWN',
'SYK', 'T', 'TEAM', 'THC', 'TJX', 'TMO', 'TMUS', 'TSLA', 'TTD', 'TTWO',
'TXN', 'UAL', 'UBER', 'UNH', 'UNP', 'V', 'VAL', 'VNO', 'VRSK', 'VRTX',
'VZ', 'WBD', 'WDAY', 'WELL', 'WFC', 'WMT', 'WYNN', 'X', 'XEL', 'XOM', 'ZS'])

# Candidates with (ticker, name, debt_billions, sector, cik)
# CIKs looked up from SEC EDGAR
candidates = [
    # Tier 1: Massive debt (>$50B) - HIGHEST PRIORITY
    ('CMCSA', 'Comcast Corporation', 99, 'Telecom/Media', '0001166691'),
    ('DUK', 'Duke Energy', 90, 'Utilities', '0001326160'),
    ('CVS', 'CVS Health', 82, 'Healthcare', '0000064803'),
    ('SO', 'Southern Company', 74, 'Utilities', '0000092122'),
    ('USB', 'U.S. Bancorp', 78, 'Financials', '0000036104'),
    ('TFC', 'Truist Financial', 71, 'Financials', '0000092230'),
    ('ET', 'Energy Transfer LP', 64, 'Energy/MLP', '0001276187'),
    ('PCG', 'PG&E Corporation', 60, 'Utilities', '0001004980'),
    ('PNC', 'PNC Financial Services', 62, 'Financials', '0000713676'),
    ('BMY', 'Bristol-Myers Squibb', 51, 'Healthcare', '0000014272'),

    # Tier 2: Large debt ($30-50B) - HIGH PRIORITY
    ('D', 'Dominion Energy', 49, 'Utilities', '0000715957'),
    ('NAVI', 'Navient Corporation', 46, 'Student Loans', '0001593538'),
    ('AMT', 'American Tower', 45, 'REIT/Telecom', '0001053507'),
    ('EIX', 'Edison International', 39, 'Utilities', '0000827052'),
    ('FDX', 'FedEx Corporation', 38, 'Industrials', '0001048911'),
    ('BK', 'Bank of New York Mellon', 35, 'Financials', '0001390777'),
    ('STT', 'State Street Corporation', 35, 'Financials', '0000093751'),
    ('CI', 'Cigna Group', 34, 'Healthcare', '0001739940'),
    ('MPC', 'Marathon Petroleum', 34, 'Energy', '0001510295'),
    ('OKE', 'ONEOK Inc', 34, 'Energy/MLP', '0001039684'),
    ('EPD', 'Enterprise Products Partners', 34, 'Energy/MLP', '0001061219'),
    ('SRE', 'Sempra Energy', 33, 'Utilities', '0001032208'),
    ('KMI', 'Kinder Morgan', 32, 'Energy/MLP', '0001506307'),
    ('ELV', 'Elevance Health', 32, 'Healthcare', '0001156039'),
    ('SATS', 'EchoStar Corporation', 31, 'Telecom', '0001415404'),
    ('DELL', 'Dell Technologies', 31, 'Technology', '0001571996'),
    ('AES', 'AES Corporation', 31, 'Utilities', '0000874761'),
    ('TDG', 'TransDigm Group', 30, 'Aerospace', '0001260221'),
    ('ETR', 'Entergy Corporation', 30, 'Utilities', '0000065984'),
    ('FI', 'Fiserv Inc', 30, 'FinTech', '0000798354'),
    ('ES', 'Eversource Energy', 30, 'Utilities', '0000072741'),
    ('CCI', 'Crown Castle Inc', 30, 'REIT/Telecom', '0001051470'),

    # Tier 3: Significant debt ($15-30B) - MEDIUM PRIORITY
    ('UPS', 'United Parcel Service', 29, 'Industrials', '0001090727'),
    ('NLY', 'Annaly Capital Management', 29, 'Mortgage REIT', '0001043219'),
    ('O', 'Realty Income Corporation', 29, 'REIT', '0000726728'),
    ('WMB', 'Williams Companies', 28, 'Energy/MLP', '0000107263'),
    ('FE', 'FirstEnergy Corp', 27, 'Utilities', '0001031296'),
    ('ED', 'Consolidated Edison', 27, 'Utilities', '0001047862'),
    ('MPLX', 'MPLX LP', 26, 'Energy/MLP', '0001552275'),
    ('LNG', 'Cheniere Energy', 25, 'Energy/LNG', '0003570'),
    ('WM', 'Waste Management', 23, 'Industrials', '0000823768'),
    ('OMF', 'OneMain Financial', 22, 'Consumer Finance', '0001584207'),
    ('CNP', 'CenterPoint Energy', 22, 'Utilities', '0001130310'),
    ('PRU', 'Prudential Financial', 22, 'Insurance', '0001137774'),
    ('PSX', 'Phillips 66', 22, 'Energy', '0001534701'),
    ('MMC', 'Marsh McLennan', 21, 'Insurance', '0000062996'),
    ('WEC', 'WEC Energy Group', 21, 'Utilities', '0000783325'),
    ('EQIX', 'Equinix Inc', 21, 'REIT/Data Centers', '0001101239'),
    ('ALLY', 'Ally Financial', 20, 'Auto Finance', '0000040729'),
    ('AL', 'Air Lease Corporation', 20, 'Aircraft Leasing', '0001487712'),
    ('AEE', 'Ameren Corporation', 20, 'Utilities', '0001002910'),
    ('TGT', 'Target Corporation', 20, 'Retail', '0000027419'),
    ('MET', 'MetLife Inc', 20, 'Insurance', '0001099219'),
    ('ICE', 'Intercontinental Exchange', 20, 'Exchanges', '0001571949'),
    ('DOW', 'Dow Inc', 20, 'Chemicals', '0001751788'),
    ('DLR', 'Digital Realty Trust', 20, 'REIT/Data Centers', '0001297996'),
    ('BDX', 'Becton Dickinson', 19, 'Healthcare', '0000010795'),
    ('PPL', 'PPL Corporation', 19, 'Utilities', '0000922224'),
    ('IRM', 'Iron Mountain', 18, 'REIT', '0001020569'),
    ('APD', 'Air Products and Chemicals', 18, 'Industrials', '0000002969'),
    ('CMS', 'CMS Energy', 18, 'Utilities', '0000811156'),
    ('KMX', 'CarMax Inc', 18, 'Auto Retail', '0001170010'),
    ('BG', 'Bunge Global SA', 18, 'Agriculture', '0001996862'),
    ('VICI', 'VICI Properties', 18, 'REIT/Gaming', '0001705696'),
    ('AON', 'Aon plc', 18, 'Insurance', '0000315293'),
    ('CNC', 'Centene Corporation', 18, 'Healthcare', '0001071739'),
    ('VST', 'Vistra Corp', 18, 'Utilities', '0001692819'),
    ('TRGP', 'Targa Resources', 17, 'Energy/MLP', '0001389170'),
    ('KR', 'Kroger Co', 15, 'Retail', '0000056873'),
    ('MMM', '3M Company', 15, 'Industrials', '0000066740'),

    # Tier 4: Moderate debt ($5-15B) - SECTOR DIVERSITY
    ('HUM', 'Humana Inc', 12, 'Healthcare', '0000049071'),
    ('GIS', 'General Mills', 12, 'Consumer Staples', '0000040704'),
    ('SYY', 'Sysco Corporation', 12, 'Food Distribution', '0000096021'),
    ('GD', 'General Dynamics', 12, 'Aerospace', '0000040533'),
    ('NOC', 'Northrop Grumman', 14, 'Aerospace', '0001133421'),
    ('EMR', 'Emerson Electric', 10, 'Industrials', '0000032604'),
    ('ADM', 'Archer-Daniels-Midland', 10, 'Agriculture', '0000007084'),
    ('CAG', 'Conagra Brands', 9, 'Consumer Staples', '0000023217'),
    ('IP', 'International Paper', 8, 'Paper/Packaging', '0000051434'),
    ('SJM', 'J.M. Smucker Company', 8, 'Consumer Staples', '0000091419'),
    ('DG', 'Dollar General', 7, 'Retail', '0000029534'),
    ('K', 'Kellanova', 6, 'Consumer Staples', '0000055067'),
    ('CPB', 'Campbell Soup Company', 5, 'Consumer Staples', '0000016732'),

    # Tier 5: Special Interest (distressed/restructuring)
    ('PKG', 'Packaging Corp of America', 4, 'Packaging', '0000075677'),
    ('CLX', 'Clorox Company', 4, 'Consumer Staples', '0000021076'),
    ('SAVE', 'Spirit Airlines', 3, 'Airlines', '0001498710'),
    ('AAP', 'Advance Auto Parts', 2, 'Retail', '0001158449'),
]

# Filter out companies already in DB
not_in_db = [(t, n, d, s, c) for t, n, d, s, c in candidates if t not in in_db]

print(f'Candidates NOT in database: {len(not_in_db)}')
print()
print('='*90)
print('NEXT 100 COMPANIES TO ADD - PRIORITIZED BY CREDIT ANALYSIS VALUE')
print('='*90)
print()

tiers = [
    ('TIER 1: MASSIVE DEBT (>$50B) - HIGHEST PRIORITY', 50, 999),
    ('TIER 2: LARGE DEBT ($30-50B) - HIGH PRIORITY', 30, 50),
    ('TIER 3: SIGNIFICANT DEBT ($15-30B) - MEDIUM PRIORITY', 15, 30),
    ('TIER 4: MODERATE DEBT ($5-15B) - SECTOR DIVERSITY', 5, 15),
    ('TIER 5: SMALLER/DISTRESSED - SPECIAL INTEREST', 0, 5),
]

total = 0
for tier_name, min_d, max_d in tiers:
    tier_companies = [(t, n, d, s, c) for t, n, d, s, c in not_in_db if min_d <= d < max_d]
    if tier_companies:
        print(tier_name)
        print('-'*90)
        print(f'{"Ticker":<8} | {"Company":<35} | {"Debt":>8} | {"Sector":<20} | CIK')
        print('-'*90)
        for t, n, d, s, c in sorted(tier_companies, key=lambda x: -x[2]):
            print(f'{t:<8} | {n:<35} | ~${d:>4}B | {s:<20} | {c}')
            total += 1
        print()

print(f'TOTAL: {total} companies to add')
print()

# Generate CSV-like output for easy import
print('='*90)
print('CSV FORMAT FOR BATCH IMPORT')
print('='*90)
print('ticker,name,cik,debt_billions,sector')
for t, n, d, s, c in sorted(not_in_db, key=lambda x: -x[2]):
    print(f'{t},{n},{c},{d},{s}')
