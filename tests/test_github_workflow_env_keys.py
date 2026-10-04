from __future__ import annotations
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parent.parent
WORKFLOW=ROOT/".github"/"workflows"/"network-smoke.yml"
PROXIES={"http_proxy","https_proxy","all_proxy"}

def _envs(node):
    if isinstance(node,dict):
        for k,v in node.items():
            if str(k)=="env" and isinstance(v,dict): yield v
            yield from _envs(v)
    elif isinstance(node,list):
        for v in node: yield from _envs(v)

def test_workflow_env_keys_are_case_insensitively_unique():
    for p in sorted((ROOT/".github"/"workflows").glob("*.y*ml")):
        obj=yaml.safe_load(p.read_text(encoding="utf-8"))
        for env in _envs(obj):
            keys=[str(k).casefold() for k in env]
            assert len(keys)==len(set(keys))

def test_public_cloud_jobs_reject_proxy_without_configuring_or_clearing_it():
    jobs=yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    for name in ("public-direct-egress","public-5m-bounded-trial"):
        job=jobs[name]
        assert all(not(PROXIES & {str(k).casefold() for k in env}) for env in _envs(job))
        run="\n".join(str(s.get("run","")) for s in job["steps"])
        assert "CLOUD_PROXY_PRESENT" in run
        assert "unset HTTP_PROXY" not in run
        assert "HTTP_PROXY: ''" not in run

def test_5m_trial_is_explicit_bounded_and_effect_isolated():
    text=WORKFLOW.read_text(encoding="utf-8"); obj=yaml.safe_load(text); trial=obj["jobs"]["public-5m-bounded-trial"]
    assert "public_probe:" in text and "- 5m-trial" in text
    assert "inputs.public_probe == '5m-trial'" in str(trial["if"]) and trial["timeout-minutes"]==5
    body="\n".join(str(x) for x in trial["steps"])
    assert body.count("bs.login()")==1
    assert body.count("bs.query_history_k_data_plus")==1
    assert body.count("bs.logout()")==1
    for token in ('baostock==0.9.4','CLOUD_CANONICAL_5M_BAOSTOCK_0_9_4_BOUNDED_TRIAL_R001',
                  'code="sh.600519"','frequency="5"','adjustflag="2"','bs.login()','bs.query_history_k_data_plus','bs.logout()',
                  'login_count','query_count','logout_count','retry_count','provider_fallback_count',
                  '2026-09-23','2026-09-24','2026-09-28','2026-09-29','2026-09-30',
                  '09:35:00','11:30:00','13:05:00','15:00:00','SESSION_OR_RIGHT_LABEL_MISMATCH',
                  'returned_fields','volume_nonnegative','amount_nonnegative','UNPROVEN_BY_CURRENT_TRIAL_CONTRACT',
                  'raw_rows_persisted','GITHUB_STEP_SUMMARY'):
        assert token in body
    for forbidden in ("akshare==","stock_zh_a_hist_min_em","actions/checkout","actions/upload-artifact","secrets.","send_email","telegram","prediction_ledger","training"):
        assert forbidden not in body.lower()

def test_private_smoke_contract_is_preserved():
    text=WORKFLOW.read_text(encoding="utf-8"); jobs=yaml.safe_load(text)["jobs"]; smoke=jobs["smoke"]
    assert "github.repository == 'leeningzzu/daily_stock_analysis-private'" in str(smoke["if"])
    assert smoke["runs-on"]=="ubuntu-latest"
    assert "cron: '0 2 * * 1-5'" in text
    body="\n".join(str(x) for x in smoke["steps"])
    for token in ("actions/checkout@v5","actions/setup-python@v6","pip install -r requirements.txt","python -m pytest -m network -q","./scripts/test.sh quick --no-notify","actions/upload-artifact@v6"):
        assert token in body
