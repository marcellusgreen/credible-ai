#!/usr/bin/env python3
"""Check if ANET and ISRG are truly debt-free."""

import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv('SEC_API_KEY')

for ticker in ['ANET', 'ISRG']:
    print(f'\n{"="*60}')
    print(f'{ticker}')
    print('='*60)

    url = 'https://api.sec-api.io'
    query = {
        'query': {
            'query_string': {
                'query': f'ticker:{ticker} AND formType:"10-K"'
            }
        },
        'from': '0',
        'size': '1',
        'sort': [{'filedAt': {'order': 'desc'}}]
    }
    response = requests.post(url, json=query, headers={'Authorization': api_key})
    data = response.json()

    if data.get('filings'):
        filing = data['filings'][0]
        print(f'10-K filed: {filing["filedAt"][:10]}')

        doc_url = None
        for doc in filing.get('documentFormatFiles', []):
            if doc.get('type') == '10-K':
                doc_url = doc['documentUrl'].replace('/ix?doc=/', '/').replace('/ix?doc=', '/')
                break

        if doc_url:
            # Fetch financial statements section
            extract_url = f'https://api.sec-api.io/extractor?url={doc_url}&item=8&type=text&token={api_key}'
            resp = requests.get(extract_url)
            text = resp.text

            # Look for liabilities section in balance sheet
            liabilities_idx = text.lower().find('liabilities')
            if liabilities_idx > 0:
                liabilities_section = text[liabilities_idx:liabilities_idx+3000]

                # Check for debt-related terms
                debt_terms = ['long-term debt', 'notes payable', 'borrowings', 'convertible',
                              'term loan', 'credit facility', 'senior notes', 'bonds']

                found_debt = False
                for term in debt_terms:
                    if term in liabilities_section.lower():
                        # Find amount
                        idx = liabilities_section.lower().find(term)
                        context = liabilities_section[max(0, idx-20):idx+150]
                        # Look for dollar amounts
                        amounts = re.findall(r'\$?\s*([\d,]+\.?\d*)\s*(?:million|billion)?', context)
                        print(f'  Found "{term}": context = ...{context[:100]}...')
                        if amounts:
                            print(f'    Amounts nearby: {amounts[:3]}')
                        found_debt = True

                if not found_debt:
                    print('  No debt terms found in liabilities section')

            # Also check total liabilities vs equity
            total_liab_match = re.search(r'total\s+liabilities[^\d]*\$?\s*([\d,]+\.?\d*)', text.lower())
            total_equity_match = re.search(r"total\s+(?:stockholders'?|shareholders'?)\s+equity[^\d]*\$?\s*([\d,]+\.?\d*)", text.lower())
            cash_match = re.search(r'cash\s+and\s+cash\s+equivalents[^\d]*\$?\s*([\d,]+\.?\d*)', text.lower())

            print(f'\n  Key Balance Sheet Items:')
            if total_liab_match:
                print(f'    Total Liabilities: ${total_liab_match.group(1)}')
            if total_equity_match:
                print(f'    Total Equity: ${total_equity_match.group(1)}')
            if cash_match:
                print(f'    Cash & Equivalents: ${cash_match.group(1)}')
