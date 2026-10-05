from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "00-daily-analysis.yml"
)


class TestDailyAnalysisStrictSchedule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def _gate_source(self):
        gate_id = self.text.index("id: strict_cn_schedule_gate")
        marker = "          python - <<'PY'\n"
        start = self.text.index(marker, gate_id) + len(marker)
        end = self.text.index("\n          PY", start)
        body = self.text[start:end]

        lines = []
        for line in body.splitlines():
            if line.startswith("          "):
                lines.append(line[10:])
            else:
                lines.append(line)

        return "\n".join(lines)


    def _evidence_flywheel_step_script(self):
        step_start = self.text.index(
            "- name: 记录 Evidence Flywheel 有界真实账本（仅人工）"
        )
        marker = "        run: |\n"
        start = self.text.index(marker, step_start) + len(marker)
        end = self.text.index(
            "\n\n      - name: 上传 Evidence Flywheel Record Receipt",
            start,
        )
        body = self.text[start:end]
        lines = []
        for line in body.splitlines():
            if line.startswith("          "):
                lines.append(line[10:])
            else:
                lines.append(line)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _working_bash():
        candidates = []
        discovered = shutil.which("bash")
        if discovered:
            candidates.append(Path(discovered))
        git_path = shutil.which("git")
        if git_path:
            git_root = Path(git_path).resolve().parent.parent
            candidates.extend(
                (
                    git_root / "bin" / "bash.exe",
                    git_root / "usr" / "bin" / "bash.exe",
                )
            )
        seen = set()
        for candidate in candidates:
            resolved = str(candidate)
            if resolved in seen or not candidate.is_file():
                continue
            seen.add(resolved)
            probe = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode == 0:
                return resolved
        return None

    def _run_gate(self, trading_day=None, raises=False):
        src_pkg = types.ModuleType("src")
        src_pkg.__path__ = []

        core_pkg = types.ModuleType("src.core")
        core_pkg.__path__ = []

        calendar_pkg = types.ModuleType("src.core.trading_calendar")

        def build_market_phase_context(**kwargs):
            self.assertEqual(kwargs["market"], "cn")
            self.assertEqual(kwargs["analysis_phase"], "auto")
            self.assertEqual(kwargs["trigger_source"], "github_schedule")

            if raises:
                raise RuntimeError("synthetic calendar failure")

            if trading_day is True:
                phase = "postmarket"
            elif trading_day is False:
                phase = "non_trading"
            else:
                phase = "unknown"

            return types.SimpleNamespace(
                is_trading_day=trading_day,
                phase=types.SimpleNamespace(value=phase),
                warnings=[],
            )

        calendar_pkg.build_market_phase_context = build_market_phase_context

        src_pkg.core = core_pkg
        core_pkg.trading_calendar = calendar_pkg

        names = (
            "src",
            "src.core",
            "src.core.trading_calendar",
        )

        old_modules = {name: sys.modules.get(name) for name in names}

        sys.modules["src"] = src_pkg
        sys.modules["src.core"] = core_pkg
        sys.modules["src.core.trading_calendar"] = calendar_pkg

        old_output = os.environ.get("GITHUB_OUTPUT")

        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_path = Path(tmp) / "github_output.txt"
                os.environ["GITHUB_OUTPUT"] = str(output_path)

                exec(
                    compile(self._gate_source(), "<workflow_gate>", "exec"),
                    {"__name__": "__main__"},
                )

                result = {}

                for line in output_path.read_text(
                    encoding="utf-8"
                ).splitlines():
                    key, value = line.split("=", 1)
                    result[key] = value

                return result

        finally:
            if old_output is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = old_output

            for name, module in old_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_exactly_one_schedule_at_1900_shanghai(self):
        import re

        crons = re.findall(
            r"(?m)^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]",
            self.text,
        )

        self.assertEqual(crons, ["0 11 * * 1-5"])
        self.assertIn(
            "北京时间 19:00 (UTC 11:00)",
            self.text,
        )

    def test_proven_xshg_trading_day_allows_analysis(self):
        result = self._run_gate(trading_day=True)
        self.assertEqual(result["allow_analysis"], "true")

    def test_false_none_and_calendar_exception_all_skip(self):
        cases = (
            ("closed", False, False),
            ("unknown", None, False),
            ("exception", None, True),
        )

        for name, value, raises in cases:
            with self.subTest(name=name):
                result = self._run_gate(
                    trading_day=value,
                    raises=raises,
                )
                self.assertEqual(
                    result["allow_analysis"],
                    "false",
                )

    def test_schedule_gate_cannot_be_bypassed_by_mutable_flag(self):
        gate_source = self._gate_source()

        self.assertIn(
            "allow_analysis = context.is_trading_day is True",
            gate_source,
        )
        self.assertIn(
            "allow_analysis = False",
            gate_source,
        )
        self.assertIn(
            "except Exception as exc:",
            gate_source,
        )
        self.assertNotIn(
            "TRADING_DAY_CHECK_ENABLED",
            gate_source,
        )

        analysis_start = self.text.index(
            "- name: 执行股票分析"
        )
        analysis_end = self.text.index(
            "- name: 上传分析报告",
            analysis_start,
        )
        analysis_block = self.text[
            analysis_start:analysis_end
        ]

        self.assertIn(
            "github.event_name != 'schedule' || "
            "steps.strict_cn_schedule_gate.outputs."
            "allow_analysis == 'true'",
            analysis_block,
        )
        self.assertIn("SKIP_NO_PUSH", self.text)

    def test_manual_force_run_semantics_are_preserved(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("force_run:", self.text)
        self.assertIn(
            "github.event.inputs.force_run",
            self.text,
        )
        self.assertIn(
            'FORCE_RUN_ARG="--force-run"',
            self.text,
        )
        self.assertIn(
            "python main.py $FORCE_RUN_ARG",
            self.text,
        )

    def test_auto_screen_product_and_bounded_manual_entry(self):
        self.assertIn("- auto-screen", self.text)
        self.assertIn("auto_screen_max_results:", self.text)
        self.assertIn("auto_screen_etf_max_results:", self.text)
        self.assertIn("default: '1'", self.text)
        self.assertIn("default: '0'", self.text)
        self.assertIn("- '10'", self.text)
        self.assertIn(
            "AUTO_SCREEN_MAX_RESULTS: ${{ github.event.inputs.auto_screen_max_results || '1' }}",
            self.text,
        )
        self.assertIn(
            "AUTO_SCREEN_ETF_MAX_RESULTS: ${{ github.event.inputs.auto_screen_etf_max_results || '0' }}",
            self.text,
        )
        self.assertIn(
            "AUTO_SCREEN_STOCK_EXCLUDED_SECTORS: ${{ vars.AUTO_SCREEN_STOCK_EXCLUDED_SECTORS || '' }}",
            self.text,
        )
        self.assertIn(
            "AUTO_SCREEN_STOCK_EXCLUDED_BOARDS: ${{ vars.AUTO_SCREEN_STOCK_EXCLUDED_BOARDS || '' }}",
            self.text,
        )
        self.assertIn(
            "AUTO_SCREEN_STOCK_PREFERRED_BOARDS: ${{ vars.AUTO_SCREEN_STOCK_PREFERRED_BOARDS || '' }}",
            self.text,
        )
        self.assertIn(
            'if [ "$MODE" = "auto-screen" ] && [ "${GITHUB_EVENT_NAME}" != "workflow_dispatch" ]; then',
            self.text,
        )
        self.assertIn("auto_screen_bounded_live:", self.text)
        self.assertIn("auto_screen_bounded_model:", self.text)
        self.assertIn(
            'AUTO_SCREEN_BOUNDED_MODEL: ${{ github.event.inputs.auto_screen_bounded_model || \'\' }}',
            self.text,
        )
        self.assertIn(
            'if [ "${{ github.event.inputs.auto_screen_bounded_live }}" = "true" ]; then',
            self.text,
        )
        self.assertIn(
            'AUTO_SCREEN_BOUNDARY_VIOLATION: bounded live requires stock max=1 and ETF max=0',
            self.text,
        )
        self.assertIn(
            'AUTO_SCREEN_BOUNDARY_VIOLATION: bounded live requires auto_screen_bounded_model',
            self.text,
        )
        self.assertIn(
            'python main.py --auto-screen --auto-screen-max-results "$AUTO_SCREEN_MAX_RESULTS" '
            '--auto-screen-etf-max-results "$AUTO_SCREEN_ETF_MAX_RESULTS" '
            '$AUTO_SCREEN_BOUNDED_LIVE_ARG --no-market-review $FORCE_RUN_ARG',
            self.text,
        )
        self.assertIn(
            "python main.py --auto-screen --auto-screen-max-results 10 "
            "--auto-screen-etf-max-results 10 --no-market-review $FORCE_RUN_ARG",
            self.text,
        )
        self.assertIn(
            "python main.py --watchlist-conditional --no-market-review $FORCE_RUN_ARG",
            self.text,
        )
        self.assertIn('WATCHLIST_HAS_CODES="false"', self.text)
        self.assertIn('WATCHLIST_HAS_CODES="true"', self.text)
        self.assertIn("P0 model-effect boundary；行情数据源 fallback 仍可用", self.text)
        self.assertIn("AUTO_SCREEN_ACCEPTANCE_RECEIPT_JSON=", self.text)
        self.assertIn("GITHUB_STEP_SUMMARY", self.text)
        self.assertIn("AUTO_SCREEN_ACCEPTANCE_RECEIPT_MISSING", self.text)

        schedule_gate = self._gate_source()
        self.assertNotIn("AUTO_SCREEN", schedule_gate)
        self.assertIn("cron: '0 11 * * 1-5'", self.text)

    def test_v25_baseline_transport_is_manual_only_and_bypasses_analysis(self):
        expected_job_guard = (
            "if: ${{ github.repository == 'leeningzzu/daily_stock_analysis-private' || "
            "(github.repository == 'leeningzzu/dsa-market-research' && "
            "github.event_name == 'workflow_dispatch' && "
            "github.event.inputs.mode == 'baseline-transport') }}"
        )
        guard_lines = [
            line.strip()
            for line in self.text.splitlines()
            if line.strip().startswith("if: ${{ github.repository ==")
        ]
        self.assertEqual(guard_lines, [expected_job_guard])
        self.assertEqual(
            self.text.count("github.repository == 'leeningzzu/dsa-market-research'"),
            1,
        )

        self.assertIn("- baseline-transport", self.text)
        self.assertIn(
            "github.event_name == 'workflow_dispatch' && github.event.inputs.mode == 'baseline-transport'",
            self.text,
        )
        self.assertIn("python -m src.services.v2_5_baseline_transport", self.text)

        start = self.text.index("- name: 发送V2.5原件运输验收（仅人工）")
        end = self.text.index("- name: 记录 Evidence Flywheel 有界真实账本（仅人工）", start)
        block = self.text[start:end]
        for key in ("EMAIL_SENDER:", "EMAIL_PASSWORD:", "EMAIL_RECEIVERS:", "EMAIL_SENDER_NAME:"):
            self.assertIn(key, block)
        for forbidden in (
            "GEMINI_API_KEY",
            "GEMINI_API_KEYS",
            "OPENAI_API_KEY",
            "TAVILY_API_KEYS",
            "TUSHARE_TOKEN",
            "BOCHA_API_KEYS",
            "R2_",
            "NOTIFICATION_",
            "python main.py",
            "research_state_runtime",
            "evidence_flywheel_runtime",
        ):
            self.assertNotIn(forbidden, block)

        analysis_start = self.text.index("- name: 执行股票分析")
        analysis_condition_end = self.text.index("        env:", analysis_start)
        analysis_condition = self.text[analysis_start:analysis_condition_end]
        self.assertIn("github.event.inputs.mode != 'baseline-transport'", analysis_condition)

        restore_start = self.text.index("- name: 恢复研究状态（默认关闭）")
        restore_end = self.text.index("        env:", restore_start)
        self.assertIn(
            "github.event.inputs.mode != 'baseline-transport'",
            self.text[restore_start:restore_end],
        )

        publish_start = self.text.index("- name: 发布研究状态（默认关闭）")
        publish_end = self.text.index("        env:", publish_start)
        self.assertIn(
            "github.event.inputs.mode != 'baseline-transport'",
            self.text[publish_start:publish_end],
        )

        schedule_gate = self._gate_source()
        self.assertNotIn("baseline-transport", schedule_gate)

    def test_evidence_flywheel_record_is_manual_single_stock_receipt_only(self):
        self.assertIn("- evidence-flywheel-record", self.text)
        self.assertIn("evidence_product_probe:", self.text)
        step_start = self.text.index(
            "- name: 记录 Evidence Flywheel 有界真实账本（仅人工）"
        )
        artifact_start = self.text.index(
            "- name: 上传 Evidence Flywheel Record Receipt",
            step_start,
        )
        smoke_start = self.text.index(
            "- name: 研究状态空检查点 Smoke（仅人工）",
            artifact_start,
        )
        step_block = self.text[step_start:artifact_start]
        artifact_block = self.text[artifact_start:smoke_start]

        self.assertIn(
            "github.event_name == 'workflow_dispatch' && github.event.inputs.mode == 'evidence-flywheel-record'",
            step_block,
        )
        self.assertIn(
            "EVIDENCE_FLYWHEEL_STOCK: ${{ github.event.inputs.p0_stock_codes || '' }}",
            step_block,
        )
        for binding in (
            "DATABASE_PATH: ./data/evidence_flywheel_record.db",
            "SQLITE_WAL_ENABLED: 'false'",
            "ENABLE_REALTIME_QUOTE: 'false'",
            "ENABLE_REALTIME_TECHNICAL_INDICATORS: 'false'",
            "PREFETCH_REALTIME_QUOTES: 'false'",
            "ENABLE_CHIP_DISTRIBUTION: 'false'",
            "ENABLE_FUNDAMENTAL_PIPELINE: 'false'",
            "MARKET_REVIEW_ENABLED: 'false'",
            "DAILY_MARKET_CONTEXT_ENABLED: 'false'",
            "REPORT_INTEGRITY_ENABLED: 'false'",
            "SEARXNG_PUBLIC_INSTANCES_ENABLED: 'false'",
            "RESEARCH_STATE_DURABILITY_ENABLED: 'false'",
            "REPORT_RENDERER_ENABLED: 'false'",
            "EVIDENCE_PRODUCT_PROBE: ${{ github.event.inputs.evidence_product_probe || 'false' }}",
        ):
            self.assertIn(binding, step_block)
        for forbidden in (
            "${{ secrets.",
            "${{ vars.",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "TUSHARE_TOKEN",
            "TICKFLOW_API_KEY",
            "LONGBRIDGE_",
            "EMAIL_PASSWORD",
            "R2_ACCESS_KEY_ID",
        ):
            self.assertNotIn(forbidden, step_block)
        self.assertIn("--single-stock-only", step_block)
        self.assertIn("--closed-world-receipt", step_block)
        self.assertIn("--product-report-receipt", step_block)
        self.assertIn("PRODUCT_ARGS+=(--product-report-receipt)", step_block)
        self.assertIn('receipt["route_boundaries"]["report_projection"] == "LOCAL_AUDIT_FILE"', step_block)
        self.assertIn('receipt["route_boundaries"]["report_projection"] == "SUPPRESSED"', step_block)
        self.assertIn('product["status"] == "PASS"', step_block)
        self.assertIn('product["runtime_database_binding"] == "EXACT_FOR_CANONICAL_BRIEF_AND_COVERAGE"', step_block)
        self.assertIn('"investor_brief.one_line_conclusion"', step_block)
        self.assertIn('"investor_brief.fused_paragraph"', step_block)
        self.assertIn('"investor_brief.coverage_text"', step_block)
        self.assertIn('semantic["actual_render_consumer_proven"] is False', step_block)
        self.assertIn('"receipt_only": not product_probe', step_block)
        self.assertIn('"report_files_created": product_probe', step_block)
        self.assertIn('--receipt-file "$RECORD_RECEIPT"', step_block)
        self.assertIn("GITHUB_STEP_SUMMARY", step_block)
        self.assertIn('RECORD_DB="data/evidence_flywheel_record.db"', step_block)
        self.assertIn(
            "trap cleanup_evidence_flywheel_record EXIT",
            step_block,
        )
        self.assertIn("rm -rf reports logs", step_block)

        self.assertIn("retention-days: 1", artifact_block)
        self.assertIn(
            "path: data/evidence_flywheel_record_receipt.json",
            artifact_block,
        )
        self.assertNotIn("reports/", artifact_block)
        self.assertNotIn("logs/", artifact_block)
        self.assertNotIn("evidence_flywheel_record.db", artifact_block)

        random_delay_end = self.text.index("- name: 检出代码")
        self.assertIn(
            "github.event.inputs.mode != 'evidence-flywheel-record'",
            self.text[:random_delay_end],
        )
        for marker, end_marker in (
            ("- name: 恢复研究状态（默认关闭）", "        env:"),
            ("- name: 执行股票分析", "        env:"),
            ("- name: 发布研究状态（默认关闭）", "        env:"),
            ("- name: 上传分析报告", "        with:"),
            ("- name: 显示运行结果", "        run:"),
        ):
            start = self.text.index(marker)
            end = self.text.index(end_marker, start)
            self.assertIn(
                "github.event.inputs.mode != 'evidence-flywheel-record'",
                self.text[start:end],
            )

        schedule_gate = self._gate_source()
        self.assertNotIn("evidence-flywheel-record", schedule_gate)


    def test_evidence_flywheel_receipt_validation_executes_both_product_modes(self):
        step_start = self.text.index(
            "- name: 记录 Evidence Flywheel 有界真实账本（仅人工）"
        )
        marker = "          python - <<'PY'\n"
        start = self.text.index(marker, step_start) + len(marker)
        end = self.text.index("\n          PY", start)
        body = self.text[start:end]
        validation_source = "\n".join(
            line[10:] if line.startswith("          ") else line
            for line in body.splitlines()
        )

        base = {
            "status": "RECORDED",
            "record_count": 1,
            "model_request_budget": 0,
            "model_request_count": 0,
            "notification_suppressed": True,
            "external_durability": "NOT_REQUESTED",
            "training_requested": False,
            "database_receipt": {
                "fresh_isolated_database": True,
                "unexpected_nonzero_table_deltas": {},
                "ledger_identities": [{}],
                "database_sha256_after_close": "a" * 64,
            },
        }
        receipt_only = {
            **base,
            "artifact_policy": {
                "database_uploaded": False,
                "logs_uploaded": False,
                "receipt_only": True,
                "report_files_created": False,
                "reports_uploaded": False,
            },
            "route_boundaries": {"report_projection": "SUPPRESSED"},
        }
        product = {
            **base,
            "artifact_policy": {
                "database_uploaded": False,
                "logs_uploaded": False,
                "receipt_only": False,
                "report_files_created": True,
                "reports_uploaded": False,
            },
            "route_boundaries": {"report_projection": "LOCAL_AUDIT_FILE"},
            "product_report_receipt": {
                "status": "PASS",
                "delivery_fact_hash": "b" * 64,
                "investor_brief_hash": "c" * 64,
                "runtime_database_binding": "EXACT_FOR_CANONICAL_BRIEF_AND_COVERAGE",
                "report_anchor_paths": [
                    "investor_brief.one_line_conclusion",
                    "investor_brief.fused_paragraph",
                    "investor_brief.coverage_text",
                ],
                "report_sha256": "d" * 64,
                "report_bytes": 123,
                "v25_semantic_coverage": {
                    "rendered": False,
                    "actual_render_consumer_proven": False,
                    "slot_state_counts": {"EVIDENCE_AVAILABLE": 1},
                },
            },
        }

        for name, probe, payload in (
            ("receipt-only", False, receipt_only),
            ("product", True, product),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                receipt_path = Path(tmp) / "receipt.json"
                receipt_path.write_text(
                    __import__("json").dumps(payload),
                    encoding="utf-8",
                )
                env = os.environ.copy()
                env.update(
                    {
                        "RECORD_RECEIPT": str(receipt_path),
                        "EVIDENCE_PRODUCT_PROBE": "true" if probe else "false",
                    }
                )
                completed = subprocess.run(
                    [sys.executable, "-c", validation_source],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    msg=completed.stderr or completed.stdout,
                )


    def test_evidence_flywheel_record_cleanup_is_failure_safe(self):
        script = self._evidence_flywheel_step_script()
        self.assertLess(
            script.index("trap cleanup_evidence_flywheel_record EXIT"),
            script.index("python -m src.services.evidence_flywheel_runtime"),
        )
        bash = self._working_bash()
        if bash is None:
            self.skipTest("a working bash executable is required for the cleanup test")

        fake_receipt = (
            '{"status":"RECORDED","record_count":1,'
            '"model_request_budget":0,"model_request_count":0,'
            '"notification_suppressed":true,'
            '"external_durability":"NOT_REQUESTED",'
            '"training_requested":false,'
            '"artifact_policy":{"database_uploaded":false,'
            '"logs_uploaded":false,"receipt_only":true,'
            '"report_files_created":false,"reports_uploaded":false},'
            '"database_receipt":{"fresh_isolated_database":true,'
            '"unexpected_nonzero_table_deltas":{},'
            '"ledger_identities":[{}],'
            '"database_sha256_after_close":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}'
        )

        cases = (
            ("runtime-failure", 19, 0, 19),
            ("validation-failure", 0, 23, 23),
            ("success", 0, 0, 0),
        )
        for name, runtime_exit, validation_exit, expected_exit in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                fake_python = bin_dir / "python"
                fake_python.write_text(
                    "#!/usr/bin/env bash\n"
                    "if [ \"${1:-}\" = \"-m\" ]; then\n"
                    "  mkdir -p data reports logs\n"
                    "  : > data/evidence_flywheel_record.db\n"
                    "  : > data/evidence_flywheel_record.db-wal\n"
                    "  : > data/evidence_flywheel_record.db-shm\n"
                    f"  printf '%s\\n' '{fake_receipt}' > "
                    "data/evidence_flywheel_record_receipt.json\n"
                    "  : > reports/private.md\n"
                    "  : > logs/private.log\n"
                    "  exit \"${FAKE_RUNTIME_EXIT:-0}\"\n"
                    "fi\n"
                    "if [ \"${1:-}\" = \"-\" ]; then\n"
                    "  exit \"${FAKE_VALIDATION_EXIT:-0}\"\n"
                    "fi\n"
                    "exit 0\n",
                    encoding="utf-8",
                    newline="\n",
                )
                fake_python.chmod(0o755)
                environment = os.environ.copy()
                environment.update(
                    {
                        "PATH": str(bin_dir)
                        + os.pathsep
                        + environment.get("PATH", ""),
                        "FAKE_RUNTIME_EXIT": str(runtime_exit),
                        "FAKE_VALIDATION_EXIT": str(validation_exit),
                        "EVIDENCE_FLYWHEEL_STOCK": "600519",
                        "GITHUB_SHA": "1" * 40,
                        "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
                    }
                )
                completed = subprocess.run(
                    [bash, "--noprofile", "--norc", "-c", script],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(completed.returncode, expected_exit)
                self.assertFalse((root / "data/evidence_flywheel_record.db").exists())
                self.assertFalse((root / "data/evidence_flywheel_record.db-wal").exists())
                self.assertFalse((root / "data/evidence_flywheel_record.db-shm").exists())
                self.assertFalse((root / "reports").exists())
                self.assertFalse((root / "logs").exists())
                receipt = root / "data/evidence_flywheel_record_receipt.json"
                if expected_exit == 0:
                    self.assertTrue(receipt.is_file())
                    self.assertIn(
                        '"status":"RECORDED"',
                        receipt.read_text(encoding="utf-8"),
                    )
                    self.assertIn(
                        '"status":"RECORDED"',
                        (root / "summary.md").read_text(encoding="utf-8"),
                    )
                else:
                    self.assertFalse(receipt.exists())

    def test_p0_input_is_manual_only_and_does_not_change_the_schedule_gate(self):
        self.assertIn("p0_stock_codes:", self.text)
        self.assertIn(
            "P0_STOCK_CODES: ${{ github.event.inputs.p0_stock_codes || '' }}",
            self.text,
        )
        self.assertIn(
            'if [ "${GITHUB_EVENT_NAME}" = "workflow_dispatch" ] && '
            '[ -n "${P0_STOCK_CODES:-}" ]; then',
            self.text,
        )
        self.assertIn(
            'python main.py --p0-bounded-trial --stocks "$P0_STOCK_CODES" '
            '--no-market-review $FORCE_RUN_ARG',
            self.text,
        )

        schedule_gate = self._gate_source()
        self.assertNotIn("P0_STOCK_CODES", schedule_gate)
        self.assertNotIn("p0_stock_codes", schedule_gate)

    def test_p0_input_never_reads_or_overwrites_stock_list_when_active(self):
        active_start = self.text.index("# P0 有界验收只消费本次 workflow_dispatch 输入")
        active_end = self.text.index("# 处理 LITELLM YAML 配置文件", active_start)
        binding = self.text[active_start:active_end]

        self.assertIn('P0_BOUNDED_TRIAL="true"', binding)
        self.assertIn("unset STOCK_LIST STOCK_LIST_CONFIG", binding)
        self.assertIn("else", binding)
        self.assertIn('export STOCK_LIST="$STOCK_LIST_CONFIG"', binding)
        self.assertLess(
            binding.index('P0_BOUNDED_TRIAL="true"'),
            binding.index("else"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
