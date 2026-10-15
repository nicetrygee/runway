"""Per-field scorers for the Quick Add eval. Pure: no I/O, no model.

Rules (D15, slice E0.3):
- exact match for factual fields (person, due_date, stream)
- within one bucket step for effort_minutes
- exact match or a listed `acceptable` answer for judgement fields
  (priority, mode, item_type)
title, blast_radius, sprint and notes are free text and not scored in E0.
"""

EFFORT_STEPS = [5, 30, 60, 120, 240, 480]

EXACT_FIELDS = ("person", "due_date", "stream")
JUDGEMENT_FIELDS = ("item_type", "priority", "mode")
SCORED_FIELDS = ("item_type", "priority", "effort_minutes", "person", "stream", "mode",
                 "due_date")


def score_exact(actual, expected):
    return actual == expected


def score_effort(actual, expected):
    if actual not in EFFORT_STEPS or expected not in EFFORT_STEPS:
        return False
    return abs(EFFORT_STEPS.index(actual) - EFFORT_STEPS.index(expected)) <= 1


def score_judgement(actual, expected, acceptable):
    return actual == expected or actual in (acceptable or [])


def score_case(case, output):
    """Score one case. `output` is the parsed ExtractedTask as a dict, or None
    if the model's reply didn't parse — then every field fails."""
    if output is None:
        return {field: False for field in SCORED_FIELDS}
    expected = case["expected"]
    acceptable = case.get("acceptable", {})
    scores = {}
    for field in SCORED_FIELDS:
        if field in EXACT_FIELDS:
            scores[field] = score_exact(output[field], expected[field])
        elif field in JUDGEMENT_FIELDS:
            scores[field] = score_judgement(
                output[field], expected[field], acceptable.get(field))
        else:
            scores[field] = score_effort(output[field], expected[field])
    return scores


def summarise(results):
    """Per-field accuracy (fraction correct) across a list of score_case results."""
    if not results:
        return {}
    return {
        field: sum(1 for r in results if r[field]) / len(results)
        for field in results[0]
    }
