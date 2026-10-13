"""Router unit tests with scripted providers: policies, eligibility, manual selection, fallback rules."""
import asyncio

import pytest

from app.router.classifier import TaskClassification, UNKNOWN
from app.router.router import RoutedFailure, RoutingError
from app.schemas.chat import ChatMessage
from tests.helpers import PRICES, Clock, ScriptedProvider, bad_request, down, make_router, timeout

MSGS = [ChatMessage(role="user", content="hi")]
CODING = TaskClassification("coding", "high", ("code_fence",))
SUMMARY = TaskClassification("summarization", "medium", ("summarize_verbs",))
LOW_CODING = TaskClassification("coding", "low", ())


def gem(**kw):
    return ScriptedProvider("gemini", models=kw.pop("models", ("flash", "pro", "lite")), default=kw.pop("default", "flash"), **kw)


def plan(router, task=UNKNOWN, policy=None, provider=None, model=None, sensitive=False):
    return router.plan(task, policy, provider, model, sensitive)


def primary(p):
    return p.ordered[0].model


def run(router, p):
    return asyncio.run(router.execute(p, MSGS, "rid"))


# ---- Gemini-only and the default model ---------------------------------------------------------------

@pytest.mark.parametrize("policy", ["balanced", "cost", "quality", "latency"])
def test_single_model_setup_always_selects_it(policy):
    router = make_router({"gemini": ScriptedProvider("gemini", models=("flash",))})
    p = plan(router, CODING, policy)
    assert primary(p) == "flash" and "only one eligible model" in p.reason


def test_default_policy_comes_from_settings():
    router = make_router({"gemini": gem()}, router_default_policy="cost")
    assert plan(router).policy == "cost"


def test_unknown_task_uses_default_model():
    p = plan(make_router({"gemini": gem()}, router_task_models={"coding": "pro"}), UNKNOWN, "quality")
    assert primary(p) == "flash" and "no model mapped" in p.reason


def test_low_confidence_classification_is_treated_as_unknown():
    p = plan(make_router({"gemini": gem()}, router_task_models={"coding": "pro"}), LOW_CODING, "quality")
    assert primary(p) == "flash"


# ---- policies ------------------------------------------------------------------------------------------

def test_quality_uses_model_mapped_to_task():
    router = make_router({"gemini": gem()}, router_task_models={"coding": "pro", "summarization": "lite"})
    assert primary(plan(router, CODING, "quality")) == "pro"
    assert primary(plan(router, SUMMARY, "quality")) == "lite"
    assert primary(plan(router, TaskClassification("reasoning", "high", ()), "quality")) == "flash"  # unmapped -> default


def test_quality_with_unavailable_mapped_model_falls_back_to_default_and_says_so():
    router = make_router({"gemini": gem(models=("flash", "lite"))}, router_task_models={"coding": "pro"})
    p = plan(router, CODING, "quality")
    assert primary(p) == "flash" and "unavailable" in p.reason and "pro" in p.reason


def test_balanced_prefers_task_mapping_then_default():
    router = make_router({"gemini": gem()}, router_task_models={"coding": "pro"}, model_profiles=PRICES)
    assert primary(plan(router, CODING, "balanced")) == "pro"
    p = plan(router, UNKNOWN, "balanced")
    assert primary(p) == "flash"
    assert [c.model for c in p.ordered] == ["flash", "lite", "pro"]  # remaining ordered by known price


def test_cost_picks_cheapest_when_all_prices_known():
    p = plan(make_router({"gemini": gem()}, model_profiles=PRICES), CODING, "cost")
    assert primary(p) == "lite" and [c.model for c in p.ordered] == ["lite", "flash", "pro"]


def test_cost_with_missing_prices_uses_default_and_never_invents_a_ranking():
    router = make_router({"gemini": gem()}, model_profiles={"pro": PRICES["pro"], "flash": PRICES["flash"]})
    p = plan(router, CODING, "cost")
    assert primary(p) == "flash" and "price not configured for lite" in p.reason and "MODEL_PROFILES" in p.reason


def test_cost_needs_both_input_and_output_price():
    router = make_router({"gemini": gem(models=("flash", "pro"))}, model_profiles={"flash": {"input_cost_per_mtok": 1}, "pro": PRICES["pro"]})
    p = plan(router, CODING, "cost")
    assert primary(p) == "flash" and "price not configured for flash" in p.reason


def test_latency_uses_measured_medians_only():
    router = make_router({"gemini": gem()})
    for model, values in {"flash": [300, 310, 320], "pro": [900, 950, 1000], "lite": [100, 120, 110]}.items():
        for v in values:
            router.stats.record(("gemini", model), v)
    p = plan(router, CODING, "latency")
    assert primary(p) == "lite" and "lowest measured median latency" in p.reason


def test_latency_without_enough_measurements_falls_back_to_default():
    router = make_router({"gemini": gem()})
    router.stats.record(("gemini", "lite"), 10)  # one sample is below ROUTER_MIN_LATENCY_SAMPLES=3
    p = plan(router, CODING, "latency")
    assert primary(p) == "flash" and "not enough measurements" in p.reason


def test_successful_calls_feed_latency_stats():
    prov = gem()
    router = make_router({"gemini": prov})
    for _ in range(3):
        run(router, plan(router))
    median, n = router.stats.median(("gemini", "flash"), 3)
    assert n == 3 and median is not None and median >= 0


# ---- manual ----------------------------------------------------------------------------------------------

def test_manual_selects_the_requested_eligible_model_with_no_fallbacks():
    p = plan(make_router({"gemini": gem()}), CODING, "manual", model="pro")
    assert primary(p) == "pro" and len(p.ordered) == 1 and "you selected pro" in p.reason


def test_manual_rejects_unknown_or_unconfigured_models_listing_eligible_ones():
    router = make_router({"gemini": gem(models=("flash",))})
    with pytest.raises(RoutingError) as e:
        plan(router, CODING, "manual", model="gpt-9")
    assert e.value.code == "model_not_eligible" and e.value.status_code == 422
    assert e.value.details == {"eligible_models": ["gemini/flash"]}


def test_manual_requires_a_model():
    with pytest.raises(RoutingError) as e:
        plan(make_router({"gemini": gem()}), CODING, "manual")
    assert e.value.code == "model_required"


def test_manual_rejects_a_model_that_is_cooling_down():
    router = make_router({"gemini": gem()})
    router.tracker.record_failure(("gemini", "pro"), "model_not_found")
    with pytest.raises(RoutingError) as e:
        plan(router, CODING, "manual", model="pro")
    assert e.value.code == "model_not_eligible"


def test_manual_model_offered_by_two_providers_is_ambiguous_without_provider():
    router = make_router({"gemini": gem(models=("shared",)), "openai": ScriptedProvider("openai", models=("shared",))})
    with pytest.raises(RoutingError) as e:
        plan(router, CODING, "manual", model="shared")
    assert e.value.code == "model_ambiguous"
    assert primary(plan(router, CODING, "manual", provider="openai", model="shared")) == "shared"


def test_manual_failure_never_falls_back():
    prov = gem(script={"pro": [timeout()]})
    router = make_router({"gemini": prov}, model_profiles=PRICES)
    with pytest.raises(RoutedFailure) as e:
        run(router, plan(router, CODING, "manual", model="pro"))
    assert prov.calls == ["pro"] and e.value.error.code == "provider_timeout"


# ---- eligibility -----------------------------------------------------------------------------------------

def test_no_credentials_means_provider_not_configured_and_no_calls():
    prov = gem(configured=False)
    with pytest.raises(RoutingError) as e:
        plan(make_router({"gemini": prov}), CODING)
    assert (e.value.status_code, e.value.code) == (503, "provider_not_configured")
    assert "GEMINI_API_KEY" in e.value.message and prov.calls == []


def test_explicit_unconfigured_provider_is_an_error_not_a_switch():
    router = make_router({"gemini": gem(), "openai": ScriptedProvider("openai", configured=False)})
    with pytest.raises(RoutingError) as e:
        plan(router, CODING, provider="openai")
    assert e.value.code == "provider_not_configured" and "OPENAI_API_KEY" in e.value.message


def test_configured_but_unimplemented_provider_is_never_routed_to():
    stub = ScriptedProvider("openai", models=("gpt-x",), generates=False)
    router = make_router({"gemini": gem(models=("flash",)), "openai": stub})
    assert [c.provider for c in router.eligible()] == ["gemini"]
    with pytest.raises(RoutingError) as e:
        plan(router, CODING, provider="openai")
    assert (e.value.status_code, e.value.code) == (501, "provider_not_implemented")
    assert stub.calls == []


def test_allowed_providers_list_restricts_and_orders_candidates():
    both = {"gemini": gem(models=("flash",)), "openai": ScriptedProvider("openai", models=("gpt-x",))}
    assert [c.provider for c in make_router(both).eligible()] == ["gemini", "openai"]
    assert [c.provider for c in make_router(both, router_allowed_providers="openai").eligible()] == ["openai"]
    assert [c.provider for c in make_router(both, router_allowed_providers="openai,gemini").eligible()] == ["openai", "gemini"]


def test_sensitive_requests_only_go_to_sensitive_providers():
    both = {"gemini": gem(models=("flash",)), "openai": ScriptedProvider("openai", models=("gpt-x",))}
    router = make_router(both, router_sensitive_providers="gemini", router_allowed_providers="openai,gemini")
    assert primary(plan(router, CODING, sensitive=False)) == "gpt-x"
    assert primary(plan(router, CODING, sensitive=True)) == "flash"


def test_nothing_eligible_after_failures_is_a_clear_503():
    router = make_router({"gemini": gem(models=("flash",))})
    router.tracker.record_failure(("gemini", "flash"), "provider_auth_error")
    with pytest.raises(RoutingError) as e:
        plan(router, CODING)
    assert (e.value.status_code, e.value.code) == (503, "no_eligible_provider")


def test_models_cool_down_after_repeated_failures_then_recover():
    clock = Clock()
    router = make_router({"gemini": gem(models=("flash", "pro"))}, clock=clock, router_cooldown_seconds=60, router_failure_threshold=3)
    for _ in range(2):
        router.tracker.record_failure(("gemini", "pro"), "provider_timeout")
    assert router.tracker.is_available(("gemini", "pro"))
    router.tracker.record_failure(("gemini", "pro"), "provider_timeout")
    assert [c.model for c in router.eligible()] == ["flash"]
    clock.now += 61
    assert [c.model for c in router.eligible()] == ["flash", "pro"]


def test_request_specific_errors_do_not_mark_a_model_unhealthy():
    router = make_router({"gemini": gem(models=("flash",))}, router_failure_threshold=1)
    router.tracker.record_failure(("gemini", "flash"), "provider_bad_request")
    router.tracker.record_failure(("gemini", "flash"), "empty_response")
    assert router.tracker.is_available(("gemini", "flash"))


def test_success_resets_failure_count():
    router = make_router({"gemini": gem()}, router_failure_threshold=2)
    router.tracker.record_failure(("gemini", "pro"), "provider_error")
    router.tracker.record_success(("gemini", "pro"))
    router.tracker.record_failure(("gemini", "pro"), "provider_error")
    assert router.tracker.is_available(("gemini", "pro"))


# ---- fallback --------------------------------------------------------------------------------------------

def test_timeout_falls_back_to_a_cheaper_model_once():
    prov = gem(script={"pro": [timeout()]})
    router = make_router({"gemini": prov}, router_task_models={"coding": "pro"}, model_profiles=PRICES)
    out = run(router, plan(router, CODING, "quality"))
    assert prov.calls == ["pro", "flash"]  # next in line is flash (default), cheaper than pro
    info = out.info()
    assert info.fallback_used and info.selected_model == "flash"
    assert info.fallback_reason == "gemini/pro failed (provider_timeout)"
    assert [(a.model, a.outcome) for a in info.attempts] == [("pro", "provider_timeout"), ("flash", "success")]


def test_fallback_never_goes_to_a_pricier_model():
    prov = gem(models=("flash", "pro"), script={"flash": [down()]})
    router = make_router({"gemini": prov}, model_profiles=PRICES)  # pro costs 15 vs flash 3
    with pytest.raises(RoutedFailure) as e:
        run(router, plan(router, CODING, "balanced"))
    assert prov.calls == ["flash"] and e.value.error.code == "provider_error"


def test_fallback_blocked_when_prices_unknown_unless_operator_opts_in():
    prov = gem(models=("flash", "pro"), script={"flash": [timeout()]})
    router = make_router({"gemini": prov})  # default fallback mode: cheaper_or_equal, but no prices configured
    with pytest.raises(RoutedFailure):
        run(router, plan(router))
    assert prov.calls == ["flash"]

    prov2 = gem(models=("flash", "pro"), script={"flash": [timeout()]})
    router = make_router({"gemini": prov2}, router_fallback="any_eligible")
    assert run(router, plan(router)).info().fallback_used and prov2.calls == ["flash", "pro"]


def test_fallback_can_be_switched_off():
    prov = gem(script={"flash": [timeout()]})
    router = make_router({"gemini": prov}, router_fallback="off", model_profiles=PRICES)
    with pytest.raises(RoutedFailure):
        run(router, plan(router))
    assert prov.calls == ["flash"]


def test_fallback_is_bounded_by_max_fallbacks():
    prov = gem(script={m: [down()] for m in ("flash", "pro", "lite")})
    router = make_router({"gemini": prov}, router_fallback="any_eligible", router_max_fallbacks=1)
    with pytest.raises(RoutedFailure) as e:
        run(router, plan(router))
    assert len(prov.calls) == 2 and len(e.value.attempts) == 2  # primary + exactly one fallback, then stop


def test_same_model_is_never_retried():
    prov = gem(models=("flash",), script={"flash": [timeout()]})
    router = make_router({"gemini": prov}, router_fallback="any_eligible")
    with pytest.raises(RoutedFailure):
        run(router, plan(router))
    assert prov.calls == ["flash"]


def test_request_level_errors_do_not_trigger_fallback():
    prov = gem(script={"flash": [bad_request()]})
    router = make_router({"gemini": prov}, router_fallback="any_eligible")
    with pytest.raises(RoutedFailure) as e:
        run(router, plan(router))
    assert prov.calls == ["flash"] and e.value.error.code == "provider_bad_request"


def test_fallback_skips_models_that_are_cooling_down():
    prov = gem(script={"flash": [timeout()]})
    router = make_router({"gemini": prov}, router_fallback="any_eligible")
    p = plan(router)
    router.tracker.record_failure(("gemini", "pro"), "model_not_found")
    out = run(router, p)
    assert prov.calls == ["flash", "lite"] and out.selected.model == "lite"


def test_fallback_respects_the_total_time_budget():
    prov = gem(script={"flash": [timeout()]})
    router = make_router({"gemini": prov}, router_fallback="any_eligible", router_total_timeout_seconds=10, provider_timeout_seconds=30)
    with pytest.raises(RoutedFailure):
        run(router, plan(router))
    assert prov.calls == ["flash"]


def test_failures_are_recorded_for_availability():
    prov = gem(models=("flash",), script={"flash": [ProviderError_auth()]})
    router = make_router({"gemini": prov})
    with pytest.raises(RoutedFailure):
        run(router, plan(router))
    assert not router.tracker.is_available(("gemini", "flash"))  # auth failure takes it out of rotation


def ProviderError_auth():
    from app.providers.base import ProviderError

    return ProviderError(502, "provider_auth_error", "bad key")


def test_snapshot_reports_unknowns_as_null_and_hides_nothing_secret():
    router = make_router({"gemini": gem(models=("flash", "pro")), "openai": ScriptedProvider("openai", configured=False)}, model_profiles={"flash": PRICES["flash"]})
    snap = router.snapshot()
    gemini = next(p for p in snap.providers if p.name == "gemini")
    flash, pro = gemini.models
    assert flash.input_cost_per_mtok == 1.0 and pro.input_cost_per_mtok is None
    assert flash.measured_latency_ms is None and flash.latency_samples == 0
    assert snap.default_model == "flash" and snap.eligible_count == 2
    assert next(p for p in snap.providers if p.name == "openai").configured is False
