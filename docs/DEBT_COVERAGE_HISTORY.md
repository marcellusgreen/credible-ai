# Debt Coverage Backfill History (Phases 1-9 + Benchmarks)

Multi-phase effort to populate `outstanding` amounts on debt instruments and set benchmark denominators for structural blockers. Progress: 32 → 197 genuinely OK + 94 benchmark-adjusted = 314/314 accounted for.

## Phase Summary

| Phase | Script | Method | Instruments | Cost |
|-------|--------|--------|-------------|------|
| 1 | (manual SQL) | Recover amounts from cached extraction results | 291 | $0 |
| 2 | `backfill_outstanding_from_filings.py` | Gemini extracts from single debt footnote | 282 | ~$0.10 |
| 3 | `fix_excess_instruments.py` | Dedup by rate+year, deactivate matured | 1,304 deactivated | $0 |
| 4 | `backfill_outstanding_from_filings.py` | Re-extraction with broader section search | 30 | ~$0.10 |
| 5 | `extract_iterative.py --step core` | Full re-extraction for MISSING_ALL | 13/14 companies | ~$0.50 |
| 6 | `backfill_amounts_from_docs.py` | Multi-doc targeted extraction | 440 | ~$2.00 |
| 7 | `fix_excess_instruments.py --fix-llm-review` | Claude reviews EXCESS_SIGNIFICANT (>200%) | 49 deactivated, 45 cleared | ~$0.42 |
| 7.5 | `fix_excess_instruments.py` | Step 8 revolver clears + LLM review at 1.5x | 36 revolver clears + 180 deactivated, 91 cleared | ~$2.01 |
| 8 | `fix_pld_debt_amounts.py --ticker` | SEC-direct fetch + Gemini 2.5 Pro extraction | 84 instruments updated (~$89B) | ~$3.00 |
| 9 | `extract_amounts_from_indentures.py` | Regex + LLM extraction from supplemental indentures | 282 (66 regex + 216 LLM) | ~$1-2 |

## Key Learnings

**What works well:**
1. **Targeted prompts beat broad extraction** — Sending specific instrument list ("find amounts for THESE instruments") vs "extract ALL instruments" gets better matches.
2. **Multi-document iteration** — Try 10-K debt footnote first, fall back to 10-Q, then MDA/desc_securities.
3. **Rate + maturity year matching** — Simple scoring (0.5 for rate match within 0.15%, 0.5 for year match, threshold 0.8) reliably deduplicates.
4. **Instrument index matching** — Asking Gemini to return `instrument_index` (1-based reference to input list) is more reliable than post-hoc fuzzy matching.
5. **Provenance tagging** — Store `amount_source`, `amount_doc_type`, `amount_doc_date` in `attributes` JSONB.
6. **Fresh session per company** — Neon drops idle connections during 10-60s Gemini calls.
7. **Indenture regex extraction ($0 cost)** — For bullet bonds, supplemental indentures state original issuance amount. Phase 9 got 66 instruments via regex alone.

**What fails or has low yield:**
1. **Revolvers/term loans** — Usually $0 drawn; correct to skip.
2. **Aggregate-only footnotes** — Large IG issuers (VZ, T, CMCSA, etc.) present debt in maturity/rate buckets, not per-instrument. #1 structural blocker.
3. **Banks** — total_debt includes deposits/wholesale funding. Denominator is wrong, not extraction.
4. **Utility subsidiary-level debt** — Parent total_debt includes subsidiary FMBs.
5. **Base indentures vs supplemental** — Old IG issuers have 1990s-era base indentures without per-issuance amounts. LLM calls against base indentures return 0 matches ~87% of the time.
6. **Gemini key name inconsistency** — Returns `outstanding_amount_cents` instead of `outstanding_cents`. Always check for both.

## Structural Gap Categories (resolved via benchmark_total_debt)

All structural blockers handled by setting `benchmark_total_debt` = instrument sum on `company_financials`. Gap analysis uses `COALESCE(benchmark_total_debt, total_debt)` as denominator.

- **Aggregate-only footnotes** (~12 cos): VZ, T, CMCSA, PG, KO, LMT, CSX, GE, MRK, HD, QCOM, CVX
- **Banks** (~17 cos): BAC, MS, WFC, COF, USB, TFC, PNC, C, AXP, GS, JPM, SCHW, ALLY, BK, NLY, OMF, STT
- **Utilities** (~21 cos): AEE, AEP, AES, CEG, CMS, CNP, D, DUK, ED, EIX, ES, ETR, EXC, FE, NEE, PCG, PPL, SO, SRE, VST, WEC
- **Captive finance** (3 cos): F, GM, PCAR
- **Other**: Tower REITs (CCI), midstream MLPs (MPLX), stale total_debt (AIG, NEM, NRG, WELL), no financials (GFS)

## Backfill Scripts

```bash
# Phase 2/4: Extract from single debt footnote
python scripts/backfill_outstanding_from_filings.py --analyze
python scripts/backfill_outstanding_from_filings.py --fix --ticker AAPL

# Phase 3: Dedup and deactivate
python scripts/fix_excess_instruments.py --analyze
python scripts/fix_excess_instruments.py --deduplicate --dry-run
python scripts/fix_excess_instruments.py --deactivate-matured

# Phase 6: Multi-doc targeted extraction
python scripts/backfill_amounts_from_docs.py --fix [--all-missing] [--model gemini-2.5-pro]

# Phase 9: Extract from indentures (bullet bonds)
python scripts/extract_amounts_from_indentures.py --fix [--regex-only] [--all-missing]
```

## Extraction Ceiling

After 11 tiers (~$15-20 total Gemini cost): 197/291 genuinely OK (68%), plus 94 benchmark-adjusted = 291/291 accounted for. Overall $5,838B/$7,980B = 73.2%. Further LLM extraction yields <5% hit rate. To move adjusted companies to genuinely OK: (1) prospectus supplement extraction, (2) subsidiary-level extraction for utilities, (3) re-extraction with Anthropic credits.
