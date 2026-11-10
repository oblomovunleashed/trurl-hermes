"""Shared Trurl planning functions. Hermes storage and tools live in workflow.py."""
import math
import random
import re
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
AXES = ('depth', 'novelty', 'relevance', 'surprise', 'perspective_change', 'entertainment')
MODES = ('personal', 'surprise', 'deep', 'playful', 'perspective', 'open', 'wildcard')
PRESETS = {'surprise': dict(zip(AXES, (0.15, 0.2, 0.25, 0.25, 0.05, 0.1))), 'deep': dict(zip(AXES, (0.45, 0.15, 0.15, 0.05, 0.15, 0.05))), 'playful': dict(zip(AXES, (0.05, 0.15, 0.1, 0.15, 0.05, 0.5))), 'perspective': dict(zip(AXES, (0.15, 0.15, 0.1, 0.15, 0.4, 0.05))), 'wildcard': dict(zip(AXES, (0.1, 0.25, 0.05, 0.4, 0.15, 0.05)))}
ROTATION_MODES = tuple((mode for mode in PRESETS if mode != 'wildcard'))
CONNECTED_SURPRISE_POLICY = {'kind': 'connected_surprise_v1', 'min_relevance_score': 3.0, 'require_personal_connection': True, 'max_exploratory_fraction': 0.25, 'allow_distant': False}
OPEN_EXPLORATION_POLICY = {'kind': 'open_exploration_v1', 'min_relevance_score': 3.0, 'require_personal_connection': True, 'max_open_fraction': 0.25, 'max_exploratory_fraction': 0.25, 'allow_distant': True}
LANES = {'personal': {'familiar': 0.75, 'adjacent': 0.25, 'distant': 0.0}, 'surprise': {'familiar': 0.75, 'adjacent': 0.25, 'distant': 0.0}, 'deep': {'familiar': 0.7, 'adjacent': 0.3, 'distant': 0.0}, 'playful': {'familiar': 0.65, 'adjacent': 0.35, 'distant': 0.0}, 'perspective': {'familiar': 0.55, 'adjacent': 0.45, 'distant': 0.0}, 'open': {'familiar': 0.5, 'adjacent': 0.25, 'distant': 0.25}, 'wildcard': {'familiar': 0.05, 'adjacent': 0.25, 'distant': 0.7}}
FIXED_PATTERNS = ("\\b(?:do not|don't|dont|never)\\s+(?:surprise|randomi[sz]e)\\b", "\\b(?:do not|don't|dont|never)\\s+(?:change|vary)\\s+(?:my |the |these )?(?:weights|weighting)\\b", '\\bnot\\s+surprise\\s+me\\b', '\\bno\\s+(?:surprises|randomness|randomization|randomisation|variation)\\b', '\\bnot\\s+(?:looking|asking)\\s+for\\s+surprises\\b', '\\b(?:use|keep|stick (?:to|with))\\s+(?:(?:my|the)\\s+)?(?:normal|usual|fixed|baseline|standard|same|unchanged)\\s+(?:weights|weighting|mix|priorities)\\b', '\\b(?:fixed|same|unchanged)\\s+(?:weights|weighting|mix|priorities)\\b', '\\b(?:keep|leave)\\s+(?:(?:my|the)\\s+)?(?:weights|weighting|mix|priorities)\\s+(?:the\\s+)?(?:same|unchanged)\\b')
INTENTS = (('open', '\\btry (?:a |an )?new area\\b|\\bexplore (?:an? )?unfamiliar field\\b|\\bopen exploration\\b'), ('wildcard', '\\bcompletely random\\b|\\bunrelated wildcards?\\b|\\b(?:take|push|send) me completely outside (?:my |the )?interests\\b|\\b(?:ignore|disregard) my interests\\b'), ('surprise', '\\bsurprise me\\b|\\b(?:outside|beyond) (?:my |the )?comfort zone\\b|\\b(?:outside|beyond) (?:my |the )?(?:usual |normal )?interests\\b|\\bcomplete(?:ly)? surprise\\b|\\bbroaden my (?:world|horizons)\\b'), ('deep', '\\b(?:go deep(?:er)?|deep dive|more advanced|advanced material|intellectually challenging|rigorous)\\b'), ('playful', '\\b(?:make me laugh|entertain me|something funny|something fun|playful|lighthearted|light-hearted)\\b'), ('perspective', '\\b(?:change my perspective|challenge my thinking|challenge my assumptions)\\b'))
OPEN_NEGATIONS = ('\\b(?:no|not)\\s+(?:open|distant|unrelated|independent)\\s+(?:exploration|discoveries|draws|picks|items)\\b', "\\b(?:do not|don't|dont|never)\\s+(?:try (?:a |an )?new area|explore (?:an? )?unfamiliar field|(?:use|enable|include|add|give me) (?:open|distant|unrelated|independent) (?:exploration|discoveries|draws|picks|items))\\b", '\\b(?:skip|avoid|exclude|disable)\\s+(?:all\\s+)?(?:open|distant|unrelated|independent)\\s+(?:exploration|discoveries|draws|picks|items)\\b', '\\b(?:stay|keep(?: it| this| the selection| the recommendations)?)\\s+(?:close|near)\\s+to\\s+(?:my |the )?(?:(?:existing|current|usual|known|established)\\s+)?interests\\b', '\\b(?:stick|keep|stay)\\s+(?:to|with|within)\\s+(?:my |the )?(?:(?:existing|current|usual|known|established)\\s+)?interests\\b')
WILDCARD_NEGATIONS = ('\\b(?:not|never)\\s+(?:an?\\s+)?(?:something\\s+)?completely random\\b', '\\b(?:no|not)\\s+(?:an?\\s+)?unrelated wildcards?\\b', "\\b(?:do not|don't|dont|never)\\s+(?:ignore|disregard)\\s+(?:my|the)\\s+interests\\b", "\\b(?:do not|don't|dont|never)\\s+(?:take|push|send) me completely outside (?:my |the )?interests\\b", "\\b(?:do not|don't|dont|never)\\s+(?:give|show|choose)\\s+(?:me\\s+)?(?:something\\s+)?completely random\\b", "\\b(?:do not|don't|dont|never)\\s+(?:give|show|choose)\\s+(?:me\\s+)?(?:an?\\s+)?unrelated wildcards?\\b")

def normalize_weights(values):
    """Validate a complete nonnegative finite six-axis mapping and normalize it."""
    if not isinstance(values, dict) or set(values) != set(AXES):
        raise ValueError('weights must contain exactly: ' + ', '.join(AXES))
    result = {}
    for axis in AXES:
        value = values[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('weight for %s must be a finite number' % axis)
        if not math.isfinite(value) or value < 0:
            raise ValueError('weight for %s must be finite and nonnegative' % axis)
        result[axis] = float(value)
    total = math.fsum(result.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError('weights must have a finite positive sum')
    return {axis: result[axis] / total for axis in AXES}

def distance(left, right):
    return math.fsum((abs(left[a] - right[a]) for a in AXES))

def _text(request):
    return ' '.join(request.lower().replace('’', "'").split())

def _wildcard_intent_text(text):
    """A fit-preserving negation blocks wildcard inference, not surprise."""
    return '' if any((re.search(pattern, text) for pattern in WILDCARD_NEGATIONS)) else text

def _open_intent_text(text):
    return '' if any((re.search(pattern, text) for pattern in OPEN_NEGATIONS)) else text

def _closes_open(request):
    """A current explicit opt-out overrides standing permission for one batch."""
    text = _text(request)
    return any((re.search(pattern, text) for pattern in OPEN_NEGATIONS))

def _factual_request(text):
    return bool(re.search('\\b(?:fact[- ]?check|verify|calculate|summari[sz]e|explain|what is|what are|how does|research question)\\b', text))

def _resolve_scope(request, context, requested_scope):
    """Conservative lexical fallback; an agent can supply semantic scope.

    Unrecognized language is focused. A small known broad vocabulary prevents a
    generic phrase followed by an unrecognized named subject from rotating.
    """
    if requested_scope != 'auto':
        return requested_scope
    text = _text(request)
    if not text:
        return 'broad' if context == 'weekly' else 'focused'
    if _factual_request(text) or re.search('\\b(?:about|on|regarding|specifically|only|exactly|under|within|less than|at most)\\b|\\b\\d+\\s*(?:minutes?|mins?|hours?|hrs?)\\b', text):
        return 'focused'
    if re.fullmatch('(?:please\\s+|can you\\s+|could you\\s+)?(?:surprise me|something outside my (?:usual|normal) interests)[.!]?', text):
        return 'broad'
    if any((intent == 'wildcard' and re.search(pattern, _wildcard_intent_text(text)) for intent, pattern in INTENTS)):
        return 'broad'
    if any((intent == 'open' and re.search(pattern, _open_intent_text(text)) for intent, pattern in INTENTS)):
        return 'broad'
    broad_signal = re.search('\\b(?:recommendations|selection|curation|ideas|discoveries|idea stimulation|things worth (?:my |the )?time|something interesting)\\b|\\b(?:a few|some|several) (?:videos|books|papers|films)\\b', text)
    vocabulary = set('a an few some several recommendations recommendation selection curation ideas idea discoveries stimulation things worth my the time something interesting new worthwhile personal weekly sunday week this weeks for me please give show pick prepare make suggest recommend curate can could you i want would like to see explore discover anything broad mix different topics fields across disciplines of and videos books papers films'.split())
    words = set(re.findall('[a-z]+', text))
    return 'broad' if broad_signal and words <= vocabulary else 'focused'

def _requested_mode(request, requested_mode, fixed, allow_intent=True):
    text = _text(request)
    if fixed:
        return ('personal', 'fixed_flag', False)
    if requested_mode != 'auto':
        if requested_mode in {'open', 'wildcard'} and _closes_open(request):
            return ('personal', 'current_request_closes_open', False)
        return (requested_mode, 'explicit_mode', False)
    if any((re.search(pattern, text) for pattern in FIXED_PATTERNS)):
        return ('personal', 'fixed_request', False)
    if _factual_request(text):
        return ('personal', 'constrained_factual_request', False)
    if allow_intent:
        for intent, pattern in INTENTS:
            if intent in {'open', 'wildcard'} and _closes_open(request):
                continue
            candidate_text = _wildcard_intent_text(text) if intent == 'wildcard' else _open_intent_text(text) if intent == 'open' else text
            if re.search(pattern, candidate_text):
                return (intent, 'request_intent:' + intent, False)
    if _closes_open(request):
        return ('personal', 'current_request_closes_open', False)
    return ('personal', 'personal_default', True)

def _vary(target, rng, mode):
    samples = {a: rng.gammavariate(max(target[a] * 36.0, 0.1), 1.0) for a in AXES}
    samples = normalize_weights(samples)
    result = {a: 0.8 * target[a] + 0.2 * samples[a] for a in AXES}
    if mode == 'wildcard' and result['relevance'] > 0.08:
        old = result['relevance']
        result['relevance'] = 0.08
        factor = (1.0 - 0.08) / (1.0 - old)
        for axis in AXES:
            if axis != 'relevance':
                result[axis] *= factor
    return normalize_weights(result)

def make_plan(profile, request='', context='on_demand', mode='auto', seed=None, fixed=False, repeat_count=0, scope='auto'):
    """Return a replayable batch plan; never modify profile or any file.

    repeat_count counts prior consecutive broad on-demand requests. A host agent
    derives it from the batch ledger. At two prior broad requests, rotate.
    """
    if context not in ('on_demand', 'weekly'):
        raise ValueError('context must be on_demand or weekly')
    if mode not in ('auto',) + MODES:
        raise ValueError('unknown mode: %s' % mode)
    if scope not in ('auto', 'broad', 'focused'):
        raise ValueError('scope must be auto, broad, or focused')
    if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count < 0:
        raise ValueError('repeat_count must be a nonnegative integer')
    if not isinstance(request, str):
        raise ValueError('request must be text')
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError('seed must be an integer')
    try:
        baseline = normalize_weights(profile['taste']['axes_weights'])
    except (KeyError, TypeError) as exc:
        raise ValueError('profile needs taste.axes_weights') from exc
    policy = profile.get('taste', {}).get('weighting_policy', {})
    chosen, trigger, eligible = _requested_mode(request, mode, fixed, policy.get('request_intent_overrides', True))
    resolved_scope = _resolve_scope(request, context, scope)
    if chosen == 'open' and scope == 'auto' and (not request.strip()):
        resolved_scope = 'broad'
    allowed_contexts = policy.get('variation_contexts', ['weekly_broad', 'repeated_broad_on_demand'])
    rotation = eligible and resolved_scope == 'broad' and (context == 'weekly' and 'weekly_broad' in allowed_contexts or (context == 'on_demand' and repeat_count >= 2 and ('repeated_broad_on_demand' in allowed_contexts)))
    if policy.get('mode') == 'fixed' and trigger == 'personal_default':
        rotation = False
        trigger = 'profile_fixed_policy'
    rng = None
    actual_seed = None
    if chosen not in {'personal', 'open'} or rotation:
        actual_seed = secrets.randbits(64) if seed is None else seed
        rng = random.Random(actual_seed)
    if rotation:
        candidates = [m for m in ROTATION_MODES if distance(baseline, PRESETS[m]) >= 0.35]
        chosen = rng.choice(candidates or list(ROTATION_MODES))
        trigger = 'weekly_broad_rotation' if context == 'weekly' else 'repeated_broad_rotation'
    target = dict(baseline if chosen in {'personal', 'open'} else PRESETS[chosen])
    effective = dict(baseline) if chosen in {'personal', 'open'} else _vary(target, rng, chosen)
    if rotation and distance(baseline, effective) < 0.15:
        chosen = max(ROTATION_MODES, key=lambda m: distance(baseline, PRESETS[m]))
        target = dict(PRESETS[chosen])
        effective = _vary(target, rng, chosen)
    lane_mix = dict(LANES[chosen])
    warnings = []
    request_closes_open = _closes_open(request)
    standing_open = profile.get('discovery', {}).get('open_exploration', {}).get('enabled') is True and (not request_closes_open)
    if request_closes_open:
        warnings.append('This request keeps independent exploration disabled for this batch; any stored open-exploration permission is unchanged.')
    if chosen == 'personal':
        supplied = profile.get('discovery', {}).get('lane_mix')
        if supplied is not None:
            if not isinstance(supplied, dict) or set(supplied) != {'familiar', 'adjacent', 'distant'} or any((isinstance(v, bool) or not isinstance(v, (int, float)) or (not math.isfinite(v)) or (v < 0) for v in supplied.values())) or (not math.isclose(math.fsum(supplied.values()), 1.0)):
                raise ValueError('discovery.lane_mix must be three nonnegative finite values summing to 1')
            lane_mix = dict(supplied)
            if lane_mix['distant'] and (not standing_open):
                connected_total = lane_mix['familiar'] + lane_mix['adjacent']
                lane_mix = {'familiar': lane_mix['familiar'] / connected_total, 'adjacent': lane_mix['adjacent'] / connected_total, 'distant': 0.0} if connected_total else dict(LANES['personal'])
                warnings.append('Stored distant research allocation requires explicit open-exploration opt-in; this batch reallocates it across connected lanes without changing the profile.')
    applied = chosen not in {'personal', 'open'}
    open_selection = chosen == 'open' or (chosen == 'personal' and standing_open and (lane_mix['distant'] > 0))
    return {'schema_version': 1, 'policy_version': 'connected_surprise_v1' if chosen == 'surprise' else 'open_exploration_v1' if open_selection else 'explicit_wildcard_v1' if chosen == 'wildcard' else 'standard_v1', 'mode_requested': mode, 'mode': chosen, 'trigger': trigger, 'context': context, 'scope_requested': scope, 'scope': resolved_scope, 'hypothesis_maintenance_required': resolved_scope == 'broad' and chosen != 'wildcard', 'graph_update_required': True, 'seed': actual_seed, 'baseline_weights': baseline, 'target_weights': target, 'effective_weights': effective, 'lane_mix': lane_mix, 'selection_policy': dict(CONNECTED_SURPRISE_POLICY) if chosen == 'surprise' else dict(OPEN_EXPLORATION_POLICY) if open_selection else None, 'variation_applied': applied, 'baseline_changed': False, 'rotation_distance_l1': distance(baseline, effective), 'constraints': {'request': request, 'credibility_gate': True, 'preserve_explicit_constraints': True, 'open_exploration_enabled': standing_open, 'open_exploration_disabled_for_request': request_closes_open, 'graph_max_hops': profile.get('discovery', {}).get('graph_max_hops', 2), 'weekly_delivery_enabled': bool(profile.get('delivery', {}).get('weekly', {}).get('enabled', False))}, 'randomness': {'applied': applied, 'seed': actual_seed, 'method': '80% mode target + 20% gamma-normalized draw; connected surprise retains relevance and surprise at least 20%, novelty at least 16%' if chosen == 'surprise' else '80% mode target + 20% gamma-normalized draw; explicit wildcard relevance capped at 8%' if chosen == 'wildcard' else '80% mode target + 20% gamma-normalized draw' if applied else 'none', 'strength': 0.2 if applied else 0.0}, 'warnings': warnings, 'uncertainty': ['Weights are selection priorities, not measured probabilities or guarantees of enjoyment.', 'Intent parsing is heuristic; the host agent must preserve medium, time, safety, and other explicit constraints.', 'Planning does not research recommendations, update preferences, or authorize scheduled delivery.'] + warnings}

def generic_profile(name='You', local_timezone=None):
    if local_timezone is not None:
        try:
            ZoneInfo(local_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('timezone must be a valid IANA timezone') from exc
    return {'schema_version': 1, 'person': {'id': 'local-user', 'display_name': name}, 'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'provenance': [], 'coverage': {'scope': 'No past conversations reviewed; new provisional profile.'}, 'onboarding': {'status': 'not_started', 'categories': [], 'anchors': [], 'context_sources': {source: {'permission': 'not_asked', 'scope': None, 'availability': 'unknown', 'source_provenance': []} for source in ('agent_memory', 'past_chats')}}, 'interests': [], 'taste': {'axes_weights': {a: 1.0 / len(AXES) for a in AXES}, 'weights_status': 'provisional_equal_weight_default', 'weighting_policy': {'mode': 'adaptive', 'request_intent_overrides': True, 'variation_contexts': ['weekly_broad', 'repeated_broad_on_demand'], 'preserve_baseline': True}, 'quality_gate': {'credibility_required': True}, 'pure_entertainment_allowed': True, 'presentation': {'default': 'author_title_link_table', 'kind': 'design_default'}, 'constraints': []}, 'discovery': {'lane_mix': dict(LANES['personal']), 'kind': 'design_default_not_user_preference', 'graph_max_hops': 2, 'open_exploration': {'enabled': False}}, 'media': {'enabled': ['youtube_video', 'book', 'scientific_paper', 'film', 'podcast', 'lecture'], 'music_enabled': False}, 'delivery': {'default_mode': 'on_demand', 'weekly': {'enabled': False, 'onboarding_default_pending': True, 'day': 'sunday', 'time': '20:00', 'time_confirmed': False, 'timezone': local_timezone, 'requires_timezone': local_timezone is None}}, 'controls': {'silence_is_feedback': False, 'knowledge_default': 'unknown', 'feedback_prompt': 'optional', 'cross_user_isolation': True}}
