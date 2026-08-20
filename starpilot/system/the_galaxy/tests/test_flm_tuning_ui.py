from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
TUNING_JS_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/tuning.js"
TUNING_CSS_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/tuning.css"


def _tuning_js():
  return TUNING_JS_PATH.read_text(encoding="utf-8")


def _fine_tune_render_body(source):
  start = source.index("function renderFineTuneControls()")
  return source[start:source.index("\nfunction ", start + 1)]


def test_fine_tune_panel_is_not_gated_behind_a_loaded_report():
  source = _tuning_js()
  # Applying a saved tune never loads a report, and reports can be deleted while a trial stays
  # applied, so the panel has to live in the always-rendered card rather than the report section.
  call_site = source.index("${() => renderFineTuneControls()}")
  report_section = source.index("${() => state.report ? html`")
  assert call_site < report_section


def test_fine_tune_panel_hides_when_no_rollback_baseline_exists():
  # fine_tune_active_trial rejects this state with a 409; the UI must not offer a dead button.
  body = _fine_tune_render_body(_tuning_js())
  assert 'trial.rollbackAvailable === false' in body
  assert body.index('rollbackAvailable === false') < body.index("return html`")


def test_fine_tune_draft_key_tracks_applied_values_not_timestamps():
  source = _tuning_js()
  start = source.index("function fineTuneTrialKey(")
  body = source[start:source.index("\nfunction ", start + 1)]
  # Keying on updatedAt discarded in-progress edits whenever a rename or poll refreshed the trial.
  assert "trial.updatedAt" not in body
  assert "signature" in body
  for field in ("genericParams", "vehicleKnobs", "frictionThresholds"):
    assert field in body


def test_fine_tune_styles_are_defined_for_the_rendered_classes():
  source = _tuning_js()
  styles = TUNING_CSS_PATH.read_text(encoding="utf-8")
  for class_name in ("flmFineTune", "flmFineTuneGrid", "flmFineTuneControl"):
    assert f'class="{class_name}"' in source or f'flmCardSubsection {class_name}"' in source
    assert f".{class_name}" in styles
