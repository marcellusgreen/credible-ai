"""Debt Instruments vs Legal Documents Coverage Analysis"""

import asyncio
import os
import sys
import io
from collections import defaultdict

# Handle Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import async_session_maker

async def analyze_debt_document_coverage():
    async with async_session_maker() as session:
        print("=" * 80)
        print("DEBT INSTRUMENTS vs LEGAL DOCUMENTS COVERAGE ANALYSIS")
        print("=" * 80)
        print()

        debt_query = text("""
            SELECT di.company_id, c.ticker, c.name as company_name,
                   di.instrument_type, di.name as instrument_name, di.id as instrument_id
            FROM debt_instruments di
            JOIN companies c ON di.company_id = c.id
            ORDER BY c.ticker, di.instrument_type
        """)
        debt_result = await session.execute(debt_query)
        debt_instruments = debt_result.fetchall()

        docs_query = text("""
            SELECT ds.company_id, c.ticker, ds.section_type, COUNT(*) as doc_count
            FROM document_sections ds
            JOIN companies c ON ds.company_id = c.id
            WHERE ds.section_type IN ('indenture', 'credit_agreement')
            GROUP BY ds.company_id, c.ticker, ds.section_type
            ORDER BY c.ticker
        """)
        docs_result = await session.execute(docs_query)
        legal_docs = docs_result.fetchall()

        company_debt = defaultdict(lambda: {"ticker": "", "name": "", "instruments": [], "types": defaultdict(int)})
        company_docs = defaultdict(lambda: {"indentures": 0, "credit_agreements": 0})
        
        for row in debt_instruments:
            company_id = str(row.company_id)
            company_debt[company_id]["ticker"] = row.ticker
            company_debt[company_id]["name"] = row.company_name
            company_debt[company_id]["instruments"].append({
                "id": str(row.instrument_id), "name": row.instrument_name, "type": row.instrument_type
            })
            company_debt[company_id]["types"][row.instrument_type] += 1
        
        for row in legal_docs:
            company_id = str(row.company_id)
            if row.section_type == "indenture":
                company_docs[company_id]["indentures"] = row.doc_count
            elif row.section_type == "credit_agreement":
                company_docs[company_id]["credit_agreements"] = row.doc_count

        total_debt_instruments = len(debt_instruments)
        total_indentures = sum(d["indentures"] for d in company_docs.values())
        total_credit_agreements = sum(d["credit_agreements"] for d in company_docs.values())
        total_legal_docs = total_indentures + total_credit_agreements
        
        companies_with_full_coverage = 0
        companies_with_gaps = 0
        estimated_missing_docs = 0
        
        type_counts = defaultdict(int)
        for company_id, data in company_debt.items():
            for inst_type, count in data["types"].items():
                type_counts[inst_type] += count
        
        bond_types = ["notes", "bonds", "senior_notes", "senior_secured_notes", "senior_unsecured_notes", 
                      "subordinated_notes", "convertible_notes", "debentures"]
        loan_types = ["revolving_credit_facility", "term_loan", "term_loan_a", "term_loan_b", 
                      "revolver", "credit_facility", "term_loan_c", "delayed_draw_term_loan"]
        
        total_bond_instruments = sum(type_counts.get(t, 0) for t in bond_types)
        total_loan_instruments = sum(type_counts.get(t, 0) for t in loan_types)

        print("-" * 80)
        print("COMPANY-BY-COMPANY ANALYSIS")
        print("-" * 80)
        print(f"{'Ticker':<10} {'Company Name':<30} {'Debt Inst':<10} {'Indentures':<12} {'Credit Agr':<12} {'Status':<10}")
        print("-" * 80)
        
        company_analysis = []
        for company_id, debt_data in sorted(company_debt.items(), key=lambda x: x[1]["ticker"]):
            ticker = debt_data["ticker"]
            name = debt_data["name"][:28].encode("ascii", "replace").decode("ascii")
            num_instruments = len(debt_data["instruments"])
            num_indentures = company_docs.get(company_id, {}).get("indentures", 0)
            num_credit_agreements = company_docs.get(company_id, {}).get("credit_agreements", 0)
            total_docs = num_indentures + num_credit_agreements
            
            if total_docs >= num_instruments:
                status = "OK"
                companies_with_full_coverage += 1
            else:
                status = "GAP"
                companies_with_gaps += 1
                estimated_missing_docs += (num_instruments - total_docs)
            
            company_analysis.append({
                "ticker": ticker, "name": name, "instruments": num_instruments,
                "indentures": num_indentures, "credit_agreements": num_credit_agreements,
                "status": status, "types": dict(debt_data["types"])
            })
            
            print(f"{ticker:<10} {name:<30} {num_instruments:<10} {num_indentures:<12} {num_credit_agreements:<12} {status:<10}")
        
        print("-" * 80)
        print()

        print("=" * 80)
        print("OVERALL STATISTICS")
        print("=" * 80)
        print()
        print(f"Total debt instruments in database:     {total_debt_instruments:>10}")
        print(f"Total legal documents stored:           {total_legal_docs:>10}")
        print(f"  - Indentures:                         {total_indentures:>10}")
        print(f"  - Credit Agreements:                  {total_credit_agreements:>10}")
        print()
        print(f"Companies with full coverage (docs >= debt): {companies_with_full_coverage:>5}")
        print(f"Companies with gaps (debt > docs):           {companies_with_gaps:>5}")
        print(f"Estimated debt instruments missing docs:     {estimated_missing_docs:>5}")
        print()

        print("=" * 80)
        print("INSTRUMENT TYPE BREAKDOWN")
        print("=" * 80)
        print()
        print(f"{'Instrument Type':<35} {'Count':<10} {'Expected Doc Type':<20}")
        print("-" * 65)
        
        for inst_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            if inst_type.lower() in [t.lower() for t in bond_types]:
                expected_doc = "Indenture"
            elif inst_type.lower() in [t.lower() for t in loan_types]:
                expected_doc = "Credit Agreement"
            else:
                expected_doc = "Unknown"
            print(f"{inst_type:<35} {count:<10} {expected_doc:<20}")
        
        print("-" * 65)
        print()

        print("=" * 80)
        print("BOND vs LOAN DOCUMENT COVERAGE")
        print("=" * 80)
        print()
        print("BOND-TYPE INSTRUMENTS (need Indentures):")
        print(f"  Total bond-type instruments:     {total_bond_instruments:>10}")
        print(f"  Total indentures stored:         {total_indentures:>10}")
        if total_bond_instruments > 0:
            bond_coverage = (total_indentures / total_bond_instruments) * 100
            print(f"  Coverage ratio:                  {bond_coverage:>9.1f}%")
        print()
        print("LOAN-TYPE INSTRUMENTS (need Credit Agreements):")
        print(f"  Total loan-type instruments:     {total_loan_instruments:>10}")
        print(f"  Total credit agreements stored:  {total_credit_agreements:>10}")
        if total_loan_instruments > 0:
            loan_coverage = (total_credit_agreements / total_loan_instruments) * 100
            print(f"  Coverage ratio:                  {loan_coverage:>9.1f}%")
        print()

        if companies_with_gaps > 0:
            print("=" * 80)
            print("COMPANIES WITH DOCUMENTATION GAPS (DETAILS)")
            print("=" * 80)
            print()
            
            for ca in company_analysis:
                if ca["status"] == "GAP":
                    print(f"{ca['ticker']} - {ca['name']}")
                    print(f"  Debt instruments: {ca['instruments']}")
                    total_docs = ca['indentures'] + ca['credit_agreements']
                    print(f"  Legal documents:  {total_docs} (Indentures: {ca['indentures']}, Credit Agreements: {ca['credit_agreements']})")
                    print(f"  Instrument types: {ca['types']}")
                    print()
        
        print("=" * 80)
        print("ANALYSIS COMPLETE")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(analyze_debt_document_coverage())
