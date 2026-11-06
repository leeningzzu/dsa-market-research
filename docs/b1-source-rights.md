# B1 operation-specific source-rights package

B1 keeps the existing DSA Pipeline, Registry, canonical decision and original V2.5 report. It does not add a provider, database, scheduler, model or second report authority.

## Purpose

Set `B1_RIGHTS_EVIDENCE_PATH` to one UTF-8 JSON file only when independently verified upstream terms permit the exact internal `NO_SEND_RESEARCH` operation. The file contains no API token or account credential. It records terms identity and a fail-closed grant for each admitted dataset.

Without a valid package, strict CN Stock/ordinary-ETF research stops before provider dispatch, cache/DB access, model use, report persistence or notification. A valid daily grant does not authorize 5-minute data, realtime quotes, names, fundamentals, news, storage, training or redistribution.

## Treasury G_UST_NOMINAL — run-local source evidence only

The current native parser `data_provider/treasury_yield_curve_source.py` binds `G_UST_NOMINAL` to `MARKET_GLOBAL_TRANSMISSION` and provider identity `USTreasuryYieldCurveOfficialXML`. Its caller supplies **already authorized same-run** official `home.treasury.gov` Atom XML bytes and a trusted UTC observation instant to `StockAnalysisPipeline._ingest_b1_treasury_official_response`. This method **does not request the network**; it reuses `data_object_source_observations`, the existing Registry and Rights-v2. Missing authorization or data never triggers a request or Product admission.

Data.gov Treasury dataset `015-DO-020` declares CC0; scope is only the corresponding US daily yield-curve data, not China quotes, ETF prices or BLS. An external exact operation grant must still be reviewed and bound before every acquisition. The October 30 observed response was not stored and cannot be reconstructed or backdated. The parser validates the original month URL, exact 3M/2Y/10Y fields, percent-per-year rates, percentage-point spread, completion date and raw response hash. `source_available_at` is conservatively the actual **DSA observation time**; it is not the official historical first release. Feed `Atom.updated` is only an observed feed-update field, not per-row first publication. Without independently supplied upstream revision history and vintage, both remain `UNKNOWN`; resulting source is `PARTIAL`, Product/History/PIT/training and independent action are **NOT_ADMITTED**. Synthetic contract/test positives do not license actual data.

Treasury native parser must accept only the documented Atom/OData path `entry/content/m:properties/d:FIELD` using the fixed Microsoft OData namespaces. Matching only an XML local-name is not verified source provenance. The date marker is midnight and a reference period, not a release timestamp. The run-local Pipeline context reports `source_observation_count` separately from `product_fact_count`; before formal DataObjectAdmission, the latter stays 0 even when source observations are PARTIAL.

## Dataset and consumer binding

| Asset | Daily dataset | 5-minute dataset | Consumer |
|---|---|---|---|
| Ordinary Stock | `P_DAILY` | `P_5M` | `ASSET_RESEARCH_BRIEF` |
| Ordinary ETF | `E_PRICE_1D` | `E_PRICE_5M` | `ETF_ASSET_BRIEF` |

Every grant must bind the same `operation_id`, `intended_use=NO_SEND_RESEARCH`, exact provider identity, upstream source owner, dataset, consumer, terms URI, exact terms bytes SHA-256, terms observation time, terms scope, an exact reviewed product-use clause plus its SHA-256, and four rights. A software package license or package-registry classifier is not an upstream data-use grant. Provider API/technical documentation (including `/helpdocs/api/markdown/*` and the legacy `/mainContent?file=*.md` route) is also not a data-rights grant: a worked example mentioning local files or backtesting cannot be relabeled as permission for every dataset, product use, storage, training, or redistribution. If no actual data-use terms are found, keep upstream rights `UNKNOWN` and fail closed; user authorization to run NO_SEND research is not proof of provider rights.

## Tushare stock/ETF price candidate — account-bound, not yet admitted

Transport gate (local engineering): a Token never enables the historical `http://api.tushare.pro` default. `TUSHARE_HTTP_URL` must explicitly name the exact official `https://api.tushare.pro` candidate; absent URL, plaintext HTTP, third-party mirror and redirects fail closed before any request. This is a mocked HTTPS-only code contract, **not** a verified official HTTPS REST endpoint or authorization to call one. Do not provision the Token or issue a real provider request until official HTTPS compatibility, independent stock/ETF minute account entitlement, four-dataset operation Rights-v2 and upstream revision/vintage are supported by exact evidence. Use an already lawful DSA source if that evidence cannot be obtained.


`TushareFetcher` can now emit typed raw-basis `P_DAILY`/`E_PRICE_1D` and completed canonical `P_5M`/`E_PRICE_5M` identities for SH/SZ stock and ordinary ETF routes. This is only a candidate source seam: the runtime requires a user-held Tushare account/token, daily access, separately enabled historical minute access, exact operation approval and a Rights-v2 package for each of the four datasets. The token is a Secret and never belongs in the rights package or evidence receipt.

Tushare's response hash, row hash and DSA `observed_at` bind the exact response and known-at observation only. They are not a provider revision sequence or historical publication vintage. Unless an authoritative provider field or separate upstream record supplies revision/vintage, both remain `UNKNOWN`; DataObjectAdmission must return `UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN` and keep Product, storage, training and redistribution not admitted. A successful API call, paid permission, personal non-commercial service license or local unit test never by itself authorizes email delivery, raw persistence, PIT backfill or model use.

## Exact package shape

```json
{
  "schema_version": "b1-operation-rights-context-v1",
  "operation_id": "operator-defined-stable-operation-id",
  "intended_use": "NO_SEND_RESEARCH",
  "allowed_instruments": ["600519", "510300"],
  "rights_by_dataset": {
    "P_DAILY": {
      "schema_version": "source-rights-evidence-v2",
      "operation_id": "operator-defined-stable-operation-id",
      "intended_use": "NO_SEND_RESEARCH",
      "provider_identity": "EXACT_FETCHER_NAME",
      "upstream_source_owner": "EXACT_UPSTREAM_OWNER",
      "upstream_rights_state": "VERIFIED",
      "dataset_id": "P_DAILY",
      "consumer_scope": "ASSET_RESEARCH_BRIEF",
      "terms_uri": "https://authoritative.example/terms",
      "terms_sha256": "64-hex-sha256-of-the-observed-terms",
      "terms_observed_at": "2026-10-23T00:00:00+00:00",
      "terms_scope": "OFFICIAL_PROVIDER_DATA_USE_STATEMENT",
      "product_use_clause": "exact short clause from the observed official data-use statement",
      "product_use_clause_sha256": "64-hex-sha256-of-that-exact-clause",
      "rights": {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN"
      },
      "evidence_hash": "seal_source_rights_evidence-result"
    }
  }
}
```

The example is descriptive and is deliberately not a valid provider grant. Do not replace the placeholders with assumptions. `upstream_rights_state=VERIFIED` requires independently checked upstream data terms or an official provider data-use statement. API availability, an SDK/package license, a package-registry license classifier, a green CI run, or a user statement alone is insufficient. An `OFFICIAL_PROVIDER_DATA_USE_STATEMENT` may admit only internal Product use; storage, training, and redistribution must remain `FORBIDDEN` unless separate explicit upstream terms are observed.

## Runtime behavior

1. The package is validated and bound to one timezone-aware `research_as_of` instant.
2. Before dispatch, every requested normalized instrument must be present in `allowed_instruments`; DSA also requires both its 1d and 5m grants.
3. Provider fallback is restricted to the exact provider named by the applicable grant; daily and 5-minute grants are checked independently.
4. Existing Daily/Intraday Identity and MTF Registry produce source identity, content hash, completed-through, units and basis. Revision/vintage are carried only when the provider identity explicitly supplies them; observation time never substitutes for either field.
5. Existing `data-object-admission-receipt-v1` joins the grant to those exact runtime bytes and semantics, including Provider route, clocks, units, currency, basis, revision and vintage. Missing, unknown or mismatched data remains `NOT_OBSERVED` or `SOURCE_IDENTITY_ONLY`.
6. A target succeeds only when its original Registry rows for both required 1d and 5m datasets are `PRODUCT_ADMITTED`; otherwise no report is produced.
7. NO_SEND renders the existing complete report in memory only. It does not write report/history/ledger data or send Email/Telegram.
8. `product_use=ALLOWED` never implies storage, training, redistribution, strategy upgrade or independent action authority.

The original V2.5 HTML is not edited or regenerated by this configuration.
