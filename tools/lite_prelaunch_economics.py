"""Reproducible planning, not billing, a live load test or a retention policy.

Cloud Run unit rates are published references; all demand, GCS/egress rates,
operation counts, ads and overhead are explicit assumptions. No free tier credit.
Current ads are placeholders: the default books ZERO advertising revenue.
"""
from dataclasses import asdict, dataclass, replace
import argparse
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Inputs:
    mau: int = 10000
    days: int = 30
    paid_fraction: float = .10
    free_successes: float = 20
    paid_successes: float = 90
    free_daily_cap: int = 2
    paid_daily_cap: int = 10
    empty_ratio: float = .20
    failure_ratio: float = .05
    extra_empty_per_user: float = 0
    attempt_daily_cap: int = 30
    inference_seconds: float = 5
    vcpu: float = 2
    memory_gib: float = 4
    cpu_rate: float = .000024
    memory_rate: float = .0000025
    request_rate: float = .0000004
    class_a_rate: float = .000005
    class_b_rate: float = .0000004
    storage_rate: float = .020  # sensitivity input, NOT verified asia-east1 SKU
    egress_rate: float = .15  # market mix assumption, NOT a universal tariff
    storage_mib: float = 8
    version_factor: float = 1.25
    consent_fraction: float = .5  # independent voluntary consent, not a service gate
    selection_fraction: float = 1  # future fixed-half option OFF by default
    retention_months: int = 36  # cost horizon only; requires approved research policy
    operating_month: int = 12
    staging_days: float = 1
    soft_delete_days: float = 7
    variable_overrun: float = .20  # cold start/auth/control overhead allowance, unmeasured
    fixed_cloud: float = 20
    operating_allowance: float = 500  # planning provision; not a salary/invoice forecast
    ai_help_per_user: float = .0002  # prior 5% * 20% * 4 * $0.005 assumption
    price: float = 1.99
    fee: float = .15
    tax: float = .05
    refund: float = .02
    delivered_ads_per_free_user: float = 0  # actual delivered, not ad opportunities
    ecpm: float = 2
    ad_haircut: float = .05


def validate(x):
    for name, value in asdict(x).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(name)
    for name in ('mau', 'days', 'free_daily_cap', 'paid_daily_cap', 'attempt_daily_cap',
                 'retention_months', 'operating_month'):
        if type(getattr(x, name)) is not int:
            raise ValueError(name)
    if x.mau < 1 or not 28 <= x.days <= 31 or x.operating_month < 1:
        raise ValueError('population/time')
    for name in ('paid_fraction', 'consent_fraction', 'selection_fraction', 'fee', 'refund', 'ad_haircut'):
        if getattr(x, name) > 1:
            raise ValueError(name)
    if x.version_factor < 1:
        raise ValueError('version_factor')
    for count, cap in ((x.free_successes, x.free_daily_cap), (x.paid_successes, x.paid_daily_cap)):
        if count > cap * x.days or count * (1 + x.empty_ratio + x.failure_ratio) + x.extra_empty_per_user > x.attempt_daily_cap * x.days:
            raise ValueError('demand exceeds stated allowance; model admission separately')


def evaluate(x):
    validate(x)
    paid = x.mau * x.paid_fraction
    free = x.mau - paid
    success = free * x.free_successes + paid * x.paid_successes
    empty = success * x.empty_ratio + x.mau * x.extra_empty_per_user
    failures = success * x.failure_ratio
    attempts = success + empty + failures
    annotations = success * 1.5
    preflight = x.mau * 2
    seconds = attempts * x.inference_seconds + annotations * .2 + preflight * .1
    compute = seconds * (x.vcpu * x.cpu_rate + x.memory_gib * x.memory_rate)
    # Inherited deliberately conservative counts; must be replaced with measured traces.
    operations = ((attempts * 6 + annotations * 2 + preflight * 2) * x.request_rate
                  + (attempts * 40 + annotations * 2 + preflight * 2) * x.class_a_rate
                  + (attempts * 100 + annotations * 3 + preflight * 4) * x.class_b_rate)
    research = (success + empty) * x.consent_fraction * x.selection_fraction
    incoming = research * x.storage_mib / 1024 * x.version_factor
    # Assume even errors can incur short-lived service staging; not long-term research.
    transient_gib = (attempts - research) * x.storage_mib / 1024
    temporary = transient_gib * (x.staging_days + x.soft_delete_days) / x.days * x.storage_rate
    lifetime = x.retention_months + x.soft_delete_days / x.days
    current_storage = incoming * min(x.operating_month - .5, lifetime) * x.storage_rate
    funded_storage = incoming * lifetime * x.storage_rate
    egress = (attempts * .2 / 1024 + incoming * .25) * x.egress_rate
    variable = compute + operations + egress
    uncertainty = variable * x.variable_overrun
    common = variable + uncertainty + temporary + x.fixed_cloud + x.operating_allowance + x.mau * x.ai_help_per_user
    current_cost = common + current_storage
    funded_cost = common + funded_storage  # replaces current storage; never double counts it
    net_price = x.price / (1 + x.tax) * (1 - x.fee) * (1 - x.refund)
    subscriptions = paid * net_price
    ads = free * x.delivered_ads_per_free_user * x.ecpm / 1000 * (1 - x.ad_haircut)
    revenue = subscriptions + ads
    def ad_break_even(cost):
        denominator = free * x.ecpm / 1000 * (1 - x.ad_haircut)
        if subscriptions >= cost:
            return 0.0
        return (cost - subscriptions) / denominator if denominator else None
    return dict(inputs=asdict(x), paid_users=paid, free_users=free, successes=success,
                inference_attempts=attempts, empty_results=empty, model_failures=failures,
                research_bundles=research, new_research_gib=incoming,
                compute_cost=compute, operations_cost=operations, egress_cost=egress,
                uncertainty_allowance=uncertainty, transient_and_soft_delete_cost=temporary,
                current_storage=current_storage, future_storage_provision=funded_storage,
                net_subscription_per_user=net_price, subscriptions=subscriptions, ads=ads,
                revenue=revenue, current_cost=current_cost, funded_cost=funded_cost,
                current_profit=revenue-current_cost, funded_profit=revenue-funded_cost,
                required_delivered_ads_current=ad_break_even(current_cost),
                required_delivered_ads_funded=ad_break_even(funded_cost))


def conversion_threshold(x, funded=True):
    field = 'funded_profit' if funded else 'current_profit'
    if evaluate(replace(x, paid_fraction=0))[field] >= 0:
        return 0.0
    if evaluate(replace(x, paid_fraction=1))[field] < 0:
        return None
    lo, hi = 0., 1.
    for _ in range(60):
        mid = (lo + hi) / 2
        if evaluate(replace(x, paid_fraction=mid))[field] >= 0:
            hi = mid
        else:
            lo = mid
    return hi


def burst(jobs=100, workers=1, seconds=5, max_wait=15):
    """Deterministic warm FIFO counterfactual; no actual service/SDK/network calls.

    Reject excess admission BEFORE upload; originals must remain on clients.
    Independent retry/backoff and distributed tickets are NOT implemented by this model.
    """
    if any(type(v) is not int for v in (jobs, workers)) or min(jobs, workers) < 1:
        raise ValueError('jobs/workers')
    if not all(math.isfinite(v) for v in (seconds, max_wait)) or seconds <= 0 or max_wait < 0:
        raise ValueError('seconds/wait')
    waits = [(i // workers) * seconds for i in range(jobs)]
    admitted = [w for w in waits if w <= max_wait]
    return dict(jobs=jobs, workers=workers, seconds=seconds, max_wait=max_wait,
                admitted=len(admitted), busy_keep_local=jobs-len(admitted),
                last_admitted_finish=max(admitted)+seconds,
                naive_unbounded_last_finish=max(waits)+seconds,
                warm_upper_bound_per_minute=60*workers/seconds,
                claim='planning only; not measured Cloud Run throughput or existing admission behavior')


def scenarios():
    b = Inputs()
    changes = {
        'current_no_ad_revenue': {},
        'three_delivered_ads': {'delivered_ads_per_free_user': 3},
        'free_only_three_ads': {'paid_fraction': 0, 'delivered_ads_per_free_user': 3},
        'free_only_twenty_ads': {'paid_fraction': 0, 'delivered_ads_per_free_user': 20},
        'future_fixed_half_three_ads': {'selection_fraction': .5, 'delivered_ads_per_free_user': 3},
        'full_31_day_entitlement': {'days': 31, 'free_successes': 62, 'paid_successes': 310, 'delivered_ads_per_free_user': 3},
        'slow_20_seconds': {'inference_seconds': 20, 'delivered_ads_per_free_user': 3},
        'fee30': {'fee': .30, 'delivered_ads_per_free_user': 3},
        'storage_price_004': {'storage_rate': .04, 'delivered_ads_per_free_user': 3},
        'no_research': {'consent_fraction': 0, 'delivered_ads_per_free_user': 3},
        'all_consented': {'consent_fraction': 1, 'delivered_ads_per_free_user': 3},
        'seven_year_storage': {'retention_months': 84, 'delivered_ads_per_free_user': 3},
        'many_empty': {'empty_ratio': 1, 'failure_ratio': .2, 'delivered_ads_per_free_user': 3},
        'empty_attempt_ceiling': {'paid_fraction': 0, 'free_successes': 0, 'paid_successes': 0,
                                 'extra_empty_per_user': 900, 'delivered_ads_per_free_user': 3},
    }
    cases = {name: replace(b, **kw) for name, kw in changes.items()}
    for mau in (100, 1000, 10000):
        for fraction in (0, .05, .10):
            cases[f'mau{mau}_paid{int(fraction*100)}'] = replace(b, mau=mau, paid_fraction=fraction, delivered_ads_per_free_user=3)
    unit = {}
    for count in (90, 310):
        for months in (36, 84):
            unit[f'paid_{count}_retention{months}'] = evaluate(replace(b, mau=1, days=31,
                paid_fraction=1, paid_successes=count, fixed_cloud=0, operating_allowance=0,
                retention_months=months))
    return dict(schema='lite-prelaunch-economics/1', currency='USD', checked='2026-10-25',
                scope='OFFLINE_PLANNING_ONLY', launch_ready=False,
                scenarios={name: evaluate(x) for name, x in cases.items()}, paid_unit=unit,
                thresholds={name: dict(current=conversion_threshold(x, False), funded=conversion_threshold(x))
                            for name, x in cases.items() if name in changes and name != 'empty_attempt_ceiling'},
                bursts=[burst(workers=w, seconds=s) for w in (1, 2, 4) for s in (5, 20)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.output.open('x') as f:
        json.dump(scenarios(), f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
