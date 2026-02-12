"""Fix AAL debt instruments - add missing and update amounts from Q3 2025 10-Q"""
import os
import uuid
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse

DATABASE_URL = os.getenv('DATABASE_URL')
parsed = urlparse(DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://'))
conn = psycopg2.connect(
    host=parsed.hostname,
    port=parsed.port,
    user=parsed.username,
    password=parsed.password,
    dbname=parsed.path[1:],
    sslmode='require'
)

cur = conn.cursor()

# Get AAL company_id and issuer IDs
cur.execute('SELECT id FROM companies WHERE ticker = %s', ('AAL',))
company_id = cur.fetchone()[0]

cur.execute('''SELECT id FROM entities WHERE company_id = %s AND name = %s''',
            (company_id, 'American Airlines, Inc.'))
issuer_id = cur.fetchone()[0]

cur.execute('''SELECT id FROM entities WHERE company_id = %s AND name = %s''',
            (company_id, 'American Airlines Group Inc.'))
holdco_id = cur.fetchone()[0]

print(f'company_id: {company_id}')
print(f'issuer_id (OpCo): {issuer_id}')
print(f'holdco_id: {holdco_id}')

# Add missing instruments (amounts in cents, from Q3 2025 10-Q)
missing_instruments = [
    {
        'name': '2013 Term Loan Facility',
        'issuer_id': issuer_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'floating',
        'interest_rate': 650,
        'principal': 98000000000,
        'outstanding': 97000000000,
        'maturity_date': '2028-02-28',
    },
    {
        'name': '2014 Term Loan Facility',
        'issuer_id': issuer_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'floating',
        'interest_rate': 598,
        'principal': 117100000000,
        'outstanding': 115900000000,
        'maturity_date': '2027-01-31',
    },
    {
        'name': '2023 Term Loan Facility',
        'issuer_id': issuer_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'floating',
        'interest_rate': 626,
        'principal': 108900000000,
        'outstanding': 108900000000,
        'maturity_date': '2029-06-30',
    },
    {
        'name': 'Enhanced Equipment Trust Certificates (EETCs)',
        'issuer_id': issuer_id,
        'instrument_type': 'equipment_trust',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'fixed',
        'interest_rate': 377,
        'principal': 727100000000,
        'outstanding': 621100000000,
        'maturity_date': '2034-12-31',
    },
    {
        'name': 'Equipment Loans and Other Notes',
        'issuer_id': issuer_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'mixed',
        'interest_rate': 594,
        'principal': 409400000000,
        'outstanding': 488000000000,
        'maturity_date': '2037-12-31',
    },
    {
        'name': 'Special Facility Revenue Bonds',
        'issuer_id': issuer_id,
        'instrument_type': 'bond',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'fixed',
        'interest_rate': 381,
        'principal': 88000000000,
        'outstanding': 78900000000,
        'maturity_date': '2036-12-31',
    },
    {
        'name': 'PSP1 Promissory Note',
        'issuer_id': holdco_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_unsecured',
        'security_type': 'unsecured',
        'rate_type': 'floating',
        'interest_rate': 604,
        'principal': 175700000000,
        'outstanding': 175700000000,
        'maturity_date': '2030-04-30',
    },
    {
        'name': 'PSP2 Promissory Note',
        'issuer_id': holdco_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_unsecured',
        'security_type': 'unsecured',
        'rate_type': 'floating',
        'interest_rate': 100,
        'principal': 103000000000,
        'outstanding': 103000000000,
        'maturity_date': '2031-01-31',
    },
    {
        'name': 'PSP3 Promissory Note',
        'issuer_id': holdco_id,
        'instrument_type': 'term_loan',
        'seniority': 'senior_unsecured',
        'security_type': 'unsecured',
        'rate_type': 'floating',
        'interest_rate': 100,
        'principal': 95900000000,
        'outstanding': 95900000000,
        'maturity_date': '2031-04-30',
    },
    {
        'name': '2013 Revolving Facility',
        'issuer_id': issuer_id,
        'instrument_type': 'revolver',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'floating',
        'interest_rate': None,
        'principal': None,
        'outstanding': 0,
        'commitment': 51900000000,
        'maturity_date': '2028-02-28',
        'is_drawn': False,
    },
    {
        'name': '2014 Revolving Facility',
        'issuer_id': issuer_id,
        'instrument_type': 'revolver',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'floating',
        'interest_rate': None,
        'principal': None,
        'outstanding': 0,
        'commitment': 155700000000,
        'maturity_date': '2027-01-31',
        'is_drawn': False,
    },
    {
        'name': '2023 Revolving Facility',
        'issuer_id': issuer_id,
        'instrument_type': 'revolver',
        'seniority': 'senior_secured',
        'security_type': 'first_lien',
        'rate_type': 'floating',
        'interest_rate': None,
        'principal': None,
        'outstanding': 0,
        'commitment': 92400000000,
        'maturity_date': '2029-06-30',
        'is_drawn': False,
    },
]

for inst in missing_instruments:
    # Check if it already exists
    cur.execute('''
        SELECT id FROM debt_instruments
        WHERE company_id = %s AND name = %s
    ''', (company_id, inst['name']))
    existing = cur.fetchone()

    if existing:
        print(f'Already exists: {inst["name"]}')
        continue

    inst_id = str(uuid.uuid4())
    slug = inst['name'].lower().replace(' ', '-').replace('(', '').replace(')', '')

    cur.execute('''
        INSERT INTO debt_instruments (
            id, company_id, issuer_id, name, slug, instrument_type, seniority, security_type,
            rate_type, interest_rate, principal, outstanding, commitment, maturity_date, is_active, is_drawn
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ''', (
        inst_id, company_id, inst['issuer_id'], inst['name'], slug,
        inst.get('instrument_type'), inst.get('seniority'), inst.get('security_type'),
        inst.get('rate_type'), inst.get('interest_rate'), inst.get('principal'),
        inst.get('outstanding'), inst.get('commitment'), inst.get('maturity_date'),
        True, inst.get('is_drawn', True)
    ))
    print(f'Added: {inst["name"]}')

conn.commit()
cur.close()
conn.close()
print('Done')
