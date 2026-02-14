#!/usr/bin/env python3
"""Tier 7: Database investigation fixes for MISSING_SOME + EXCESS_SOME companies.

Applies high-confidence SQL fixes identified by parallel diagnostic investigation.
All fixes are direct SQL updates - $0 cost.
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()
import psycopg2

url = os.getenv("DATABASE_URL", "").replace("+asyncpg", "").replace("?ssl=require", "?sslmode=require")
conn = psycopg2.connect(url)
conn.autocommit = False
cur = conn.cursor()

fixes_applied = 0
companies_affected = set()

def apply_fix(description, sql, params=None):
    global fixes_applied
    print(f"\n{'='*80}")
    print(f"  {description}")
    print(f"{'='*80}")
    try:
        cur.execute(sql, params)
        rows = cur.rowcount
        print(f"  -> {rows} row(s) affected")
        fixes_applied += rows
        return rows
    except Exception as e:
        print(f"  -> ERROR: {e}")
        conn.rollback()
        return 0

# ============================================================
# BATCH A: THC - Set outstanding = principal for 3 NULL bullet bonds
# ============================================================
companies_affected.add("THC")

apply_fix("THC: 5.125% due 2027 - set outstanding = principal ($1.5B)", """
UPDATE debt_instruments
SET outstanding = principal,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "principal_as_outstanding", "fix_reason": "tier7_bullet_bond_null_outstanding"}'::jsonb
WHERE id = 'f2b5f260-5a00-42f8-9a18-91eb85b42f5a'
  AND outstanding IS NULL AND principal IS NOT NULL
""")

apply_fix("THC: 4.625% due 2028 - set outstanding = principal ($0.6B)", """
UPDATE debt_instruments
SET outstanding = principal,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "principal_as_outstanding", "fix_reason": "tier7_bullet_bond_null_outstanding"}'::jsonb
WHERE id = '4c9ddd86-2ebf-4a43-b64b-d22cf1eb0c2c'
  AND outstanding IS NULL AND principal IS NOT NULL
""")

apply_fix("THC: 6.125% due 2030 - set outstanding = principal ($2.0B)", """
UPDATE debt_instruments
SET outstanding = principal,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "principal_as_outstanding", "fix_reason": "tier7_bullet_bond_null_outstanding"}'::jsonb
WHERE id = 'd94c46ec-d3f9-4c31-a4fb-1188c8083342'
  AND outstanding IS NULL AND principal IS NOT NULL
""")

# ============================================================
# BATCH A: ROST - Reactivate wrongly deactivated 0.875% 2026 note
# ============================================================
companies_affected.add("ROST")

apply_fix("ROST: Reactivate 0.875% Senior Notes due 2026 ($0.5B, matures Sept 2026)", """
UPDATE debt_instruments
SET is_active = true,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"reactivation_reason": "tier7_wrongly_deactivated_not_matured"}'::jsonb
WHERE id = '88d54ef4-3a65-434d-893c-b8a472e78adc'
  AND is_active = false
""")

# ============================================================
# BATCH A: ROP - Deactivate 10 unnamed duplicates + transfer 1 amount
# ============================================================
companies_affected.add("ROP")

# Transfer outstanding from unnamed to named 2% due 2030
apply_fix("ROP: Transfer $0.5B to named '2% due 2030' from unnamed dup", """
UPDATE debt_instruments
SET outstanding = 50000000000,
    principal = 50000000000,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "transferred_from_unnamed_dup", "fix_reason": "tier7_dedup_transfer"}'::jsonb
WHERE id = '7c15dcf1-e2b5-403d-9e85-a1cefcefec21'
""")

# Deactivate all unnamed duplicates
rop_deactivate_ids = [
    '722f72d9-9b39-4ecb-acc6-0939f8e4c380',  # unnamed dup of 2% due 2030
    '1206c093-70bf-4dc7-9468-7dc48820ddc6',  # unnamed dup revolver 2027
    '9114534a-28a5-4a1f-ab67-f1f14bd0a18d',  # unnamed dup 4.2% 2028
    '0c8d3dcd-4806-4c4b-8407-ded39a5502a4',  # unnamed dup 4.9% 2034
    '51ce2034-541c-4561-adea-7ab52edaf624',  # unnamed dup 3.8% 2026
    'f974ade0-fb12-4bf4-bab5-9d2914dc8e30',  # unnamed dup 1.4% 2027
    '1222de16-36ca-4b58-bd53-1d28a3187f89',  # unnamed dup 2.95% 2029
    '9b521c5f-e6a5-4497-a334-1fee73b4a0ff',  # unnamed dup 4.5% 2029
    '7e06e591-6181-4c81-92c5-5241f7944b38',  # unnamed dup 1.75% 2031
    '5a125005-e7e7-46b3-8738-3361a624e4eb',  # unnamed dup 4.75% 2032
    '1f5da28d-2235-40c6-abc0-5d2a98d2db45',  # negligible unnamed "other" $2.1M
]
apply_fix(f"ROP: Deactivate {len(rop_deactivate_ids)} unnamed duplicate instruments", f"""
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"deactivation_reason": "tier7_unnamed_duplicate"}}'::jsonb
WHERE id IN ({','.join("'" + id + "'" for id in rop_deactivate_ids)})
  AND is_active = true
""")

# ============================================================
# BATCH B: DUK - Deactivate 17 duplicate instruments
# ============================================================
companies_affected.add("DUK")

# 9 FMB senior_notes duplicates (keeping senior_secured_notes versions)
duk_fmb_ids = [
    'db9c9549-3c47-4e02-a5f6-47d78ec776f6',  # 5.05% FMB 2035
    '8b0081f3-1b26-4550-88b4-717c2cf82091',  # 5.25% FMB 2035
    'dc03f46a-a0b6-49f0-86f9-3ab0caf21ee1',  # 5.55% FMB 2055
    'edc173ff-bf54-41fb-9c86-b257341b5eff',  # 5.90% FMB 2055
    '89f9f5d4-d3fa-4c36-97eb-95b04bd80f58',  # 4.35% FMB 2027
    '134b8a35-8833-4d7a-8b68-1f3a2d202e94',  # 4.85% FMB 2030
    'd2fecc75-11b9-4e59-8720-cd40c5653a64',  # 3.99% FMB 2073
    '0dcba1e2-e9e5-4457-9467-88506c50c341',  # 3.99% FMB 2074
    '39845ef6-7623-4aca-9a57-cdb71959a460',  # 5.30% FMB 2035
]
apply_fix(f"DUK: Deactivate {len(duk_fmb_ids)} FMB senior_notes duplicates", f"""
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"deactivation_reason": "tier7_fmb_type_duplicate"}}'::jsonb
WHERE id IN ({','.join("'" + id + "'" for id in duk_fmb_ids)})
  AND is_active = true
""")

# 3 generic "Secured Debt" duplicates
duk_secured_ids = [
    'ef7e92da-79bc-460c-b77b-5b964086604a',  # Secured Debt 4.89% 2048
    'aac59c95-b575-4893-aea4-ffff93a40812',  # Secured Debt 5.07% 2048
    '58b65389-8e0a-450e-9a1a-a9bb6aa02143',  # Secured Debt 4.23% 2037
]
apply_fix(f"DUK: Deactivate {len(duk_secured_ids)} generic Secured Debt duplicates", f"""
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"deactivation_reason": "tier7_generic_name_duplicate"}}'::jsonb
WHERE id IN ({','.join("'" + id + "'" for id in duk_secured_ids)})
  AND is_active = true
""")

# Duke Energy (Parent) 2.65% 2026 duplicate (keep bond with CUSIP)
apply_fix("DUK: Deactivate 'Duke Energy (Parent)' 2.65% 2026 duplicate ($1.5B)", """
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "tier7_duplicate_keep_cusip_version"}'::jsonb
WHERE id = 'ded40516-4895-4a92-96dd-ab456136afa0'
  AND is_active = true
""")

# Convertible duplicate of 4.125% bond
apply_fix("DUK: Deactivate convertible duplicate of 4.125% 2026 bond ($1.725B)", """
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "tier7_duplicate_keep_cusip_version"}'::jsonb
WHERE id = 'f562fd2c-f72f-4adc-a46a-abe6c3ae007f'
  AND is_active = true
""")

# Exact duplicate Unsecured Debt 5.70% 2035
apply_fix("DUK: Deactivate exact duplicate Unsecured Debt 5.70% 2035 ($0.075B)", """
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "tier7_exact_duplicate"}'::jsonb
WHERE id = '5fd58c25-8e03-4ee4-9127-cfb31819ccfa'
  AND is_active = true
""")

# 2 duplicate term loans at 2026-09-01
duk_tl_ids = [
    '77355845-5810-4e63-8c2d-a5fefa6b9e6d',  # Duke Energy (Parent) Term Loan
    '6410b31f-bded-4a52-8fd7-43bac6176c5c',  # Duke Energy Parent Term Loan
]
apply_fix(f"DUK: Deactivate {len(duk_tl_ids)} duplicate term loans", f"""
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"deactivation_reason": "tier7_term_loan_duplicate"}}'::jsonb
WHERE id IN ({','.join("'" + id + "'" for id in duk_tl_ids)})
  AND is_active = true
""")

# ============================================================
# BATCH B: CVX - Deactivate 2 bond duplicates
# ============================================================
companies_affected.add("CVX")

apply_fix("CVX: Deactivate duplicate 'due 2030' bond (keep 4.300% Notes, CUSIP 166756BJ4)", """
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "tier7_duplicate_keep_named_version"}'::jsonb
WHERE id = '35a5b9b8-28c2-4bd7-b3bf-5cfd1f496ea2'
  AND is_active = true
""")

apply_fix("CVX: Deactivate duplicate 'due 2028' bond (keep 4.050% Notes, CUSIP 166756BH8)", """
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "tier7_duplicate_keep_named_version"}'::jsonb
WHERE id = '34fa1e21-236c-42b2-870c-74962484f8fc'
  AND is_active = true
""")

# ============================================================
# BATCH C: TMO - Fix 100x principal scale error + copy to outstanding
# ============================================================
companies_affected.add("TMO")

apply_fix("TMO: Fix 100x principal scale error on 4.497% Notes 2030 (EUR bond)", """
UPDATE debt_instruments
SET principal = principal / 100
WHERE id = '12ad2014-2f2f-48c7-8989-2394c8c940e0'
  AND principal = 11000000000000
""")

apply_fix("TMO: Copy corrected principal to outstanding for 4.497% Notes 2030", """
UPDATE debt_instruments
SET outstanding = principal,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "tier7_principal_corrected_eur", "fix_reason": "tier7_100x_scale_fix_copy_to_outstanding"}'::jsonb
WHERE id = '12ad2014-2f2f-48c7-8989-2394c8c940e0'
  AND outstanding IS NULL
""")

# ============================================================
# BATCH C: ADBE - Merge duplicate 2.30% notes + fix amount
# ============================================================
companies_affected.add("ADBE")

apply_fix("ADBE: Deactivate duplicate 2.30% Senior Notes 2030 (no CUSIP)", """
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"deactivation_reason": "tier7_duplicate_merged_to_cusip_record"}'::jsonb
WHERE id = '3bd90d03-ba82-4fb0-aa1e-611c6283f6f7'
  AND is_active = true
""")

apply_fix("ADBE: Fix CUSIP record amount to $500M and rate to 230bps", """
UPDATE debt_instruments
SET interest_rate = 230,
    outstanding = 50000000000,
    principal = 50000000000,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "tier7_sec_10k_corrected", "fix_reason": "tier7_duplicate_merge_amount_fix"}'::jsonb
WHERE id = '2c177108-62dd-45e1-adc5-8b859c5275b1'
""")

# ============================================================
# BATCH C: IHRT - Fix term loan outstanding
# ============================================================
companies_affected.add("IHRT")

apply_fix("IHRT: Fix Term Loan from $5M to $2.382B (SEC 10-K amount)", """
UPDATE debt_instruments
SET outstanding = 238200000000,
    principal = 238200000000,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "tier7_sec_10k_corrected", "fix_reason": "tier7_term_loan_amount_fix"}'::jsonb
WHERE id = 'd7bcbdf5-6662-4e8e-b369-25da1d3eee66'
""")

# ============================================================
# BATCH D: VAL - Update outstanding for tack-on issuance
# ============================================================
companies_affected.add("VAL")

apply_fix("VAL: Update 8.375% bond outstanding from $700M to $1.085B (tack-on)", """
UPDATE debt_instruments
SET outstanding = 108520000000,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "tier7_balance_sheet_verified", "fix_reason": "tier7_tackon_issuance_update"}'::jsonb
WHERE id = '3aea0876-721e-4ff5-8551-801d3c229358'
""")

# ============================================================
# BATCH D: CHS - Set ABL outstanding from deactivated duplicate
# ============================================================
companies_affected.add("CHS")

apply_fix("CHS: Set ABL outstanding to $49M (from deactivated dup)", """
UPDATE debt_instruments
SET outstanding = 4900000000,
    attributes = COALESCE(attributes, '{}'::jsonb) || '{"amount_source": "tier7_transferred_from_deactivated_dup", "fix_reason": "tier7_abl_drawn_amount_transfer"}'::jsonb
WHERE id = '1e9fd1ee-3361-453b-8c00-bc5b5bfc88a5'
""")

# ============================================================
# BATCH E: WELL - Deactivate 7 zero-outstanding instruments
# ============================================================
companies_affected.add("WELL")

well_deactivate_ids = [
    '2680d040-7b7d-4628-baaf-431b754e3e95',  # 6.500% Notes 2041 (legacy CUSIP)
    'c55522e1-3240-4c61-b6cf-71a8edc27987',  # 5.125% Notes 2043 (legacy CUSIP)
    '83731f16-f7cb-4860-82a3-da5b246b930d',  # 5.125% Notes 2035
    'a169c844-b856-4144-82c3-af42d7dfc94e',  # 4.125% Notes 2029
    '89c8beff-d3a9-44a1-8f59-2b1d0e06d9c4',  # 2.750% Exchangeable 2028
    'd2fe9c30-7531-465e-a9b1-f3a10e1460c4',  # 3.125% Exchangeable 2029
    '14e266bc-c22a-4ad1-8aab-5a40f1877ec9',  # Unsecured Credit Facility
]
apply_fix(f"WELL: Deactivate {len(well_deactivate_ids)} zero-outstanding instruments", f"""
UPDATE debt_instruments
SET is_active = false,
    attributes = COALESCE(attributes, '{{}}'::jsonb) || '{{"deactivation_reason": "tier7_zero_outstanding_redeemed"}}'::jsonb
WHERE id IN ({','.join("'" + id + "'" for id in well_deactivate_ids)})
  AND is_active = true
  AND COALESCE(outstanding, 0) = 0
""")

# ============================================================
# COMMIT
# ============================================================
print(f"\n{'='*80}")
print(f"  SUMMARY")
print(f"{'='*80}")
print(f"  Total rows affected: {fixes_applied}")
print(f"  Companies affected: {len(companies_affected)} ({', '.join(sorted(companies_affected))})")

if '--dry-run' in sys.argv:
    print("\n  DRY RUN - Rolling back all changes")
    conn.rollback()
else:
    print("\n  COMMITTING all changes...")
    conn.commit()
    print("  COMMITTED successfully!")

cur.close()
conn.close()
print("\nDone.")
