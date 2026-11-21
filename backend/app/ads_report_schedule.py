"""Calendar-based report refresh, checkpointed per advertising profile.

Recent = seven completed local dates plus today (eight report dates).
A wider refresh satisfies narrower tiers; checkpoints use request time, never
completion time, so a slow report cannot silently skip the next time slot.
"""
from copy import deepcopy
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .models import AdsProfile

TIERS = {'recent': 8, 'daily': 14, 'weekly': 60}
NOTE = '初始化成功后自动执行：每6小时更新最近7个完整站点日＋当天；每天06:00复核最近14天，每周一06:00复核最近60天。同一时段合并为较大范围，按各广告账户当地时间执行。'

def slots(at, zone, tier):
    local = at.astimezone(ZoneInfo(zone))
    candidates = []
    for offset in range(-8, 9):
        day = local.date() + timedelta(days=offset)
        if tier == 'weekly' and day.weekday() != 0:
            continue
        for hour in ([0, 6, 12, 18] if tier == 'recent' else [6]):
            candidates.append(datetime.combine(day, time(hour), local.tzinfo).astimezone(timezone.utc))
    return max(t for t in candidates if t <= at), min(t for t in candidates if t > at)

def due_tiers(policy, profile, at):
    checkpoints = (policy or {}).get('profiles', {}).get(profile.id, {})
    due = []
    for tier in TIERS:
        previous, _ = slots(at, profile.timezone, tier)
        stamp = checkpoints.get(tier)
        if not stamp or datetime.fromisoformat(stamp) < previous:
            due.append(tier)
    return due

def next_due(db, config, at):
    if not config.enabled or not (config.ads_policy or {}).get('initialized'):
        return None
    profiles = list(db.scalars(select(AdsProfile).where(AdsProfile.account == config.store)))
    if not profiles:
        return at + timedelta(hours=1)
    if any(due_tiers(config.ads_policy, p, at) for p in profiles):
        return at
    return min(slots(at, p.timezone, tier)[1] for p in profiles for tier in TIERS)

def plan(policy, profile, at):
    due = due_tiers(policy, profile, at)
    if not due:
        return None
    days = max(TIERS[t] for t in due)
    local = at.astimezone(ZoneInfo(profile.timezone))
    first = local.date() - timedelta(days=days-1)
    start = datetime.combine(first, time(), local.tzinfo)
    return {'profile_id': profile.id, 'country': profile.country, 'timezone': profile.timezone,
            'start': start.isoformat(), 'end': at.isoformat(), 'report_days': days,
            'refresh_tiers': due, 'reports_only': days == TIERS['recent']}

def checkpoint(config, p):
    policy = deepcopy(config.ads_policy or {'version': 1})
    stamps = policy.setdefault('profiles', {}).setdefault(p['profile_id'], {})
    at = datetime.fromisoformat(p['end'])
    zone = ZoneInfo(p['timezone'])
    count = p.get('report_days') or ((at.astimezone(zone).date() - datetime.fromisoformat(p['start']).astimezone(zone).date()).days + 1)
    for tier, days in TIERS.items():
        if count >= days and (not stamps.get(tier) or at > datetime.fromisoformat(stamps[tier])):
            stamps[tier] = at.isoformat()
    config.ads_policy = policy

def finish(db, config, run, plans, at, success):
    if success and run.mode == 'initialize':
        policy = deepcopy(config.ads_policy or {'version': 1})
        first = not policy.get('initialized')
        policy['initialized'] = True
        config.ads_policy = policy
        if first and not policy.get('paused_by_user'):
            config.enabled = True
            config.revision += 1
        config.interval_minutes = 360
        for p in plans:
            # Initialization counts exact inclusive report dates, not elapsed time.
            checkpoint(config, {**p, 'report_days': min(run.days, 96)})
    if (config.ads_policy or {}).get('initialized'):
        config.next_run_at = next_due(db, config, at) if success else (at + timedelta(hours=1) if config.enabled else None)

def describe(db, config, store, at):
    profiles = list(db.scalars(select(AdsProfile).where(AdsProfile.account == store).order_by(AdsProfile.country)))
    policy = config.ads_policy or {} if config else {}
    return {'initialized': bool(policy.get('initialized')), 'note': NOTE,
            'profiles': [{'country': p.country, 'timezone': p.timezone,
                          'last_requested': policy.get('profiles', {}).get(p.id, {})} for p in profiles]}
