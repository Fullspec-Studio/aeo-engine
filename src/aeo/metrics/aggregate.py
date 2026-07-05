"""Analytics = SQL aggregations over observation rows (spec §5). Every function
returns Wilson intervals; unparseable rows are always excluded."""

from aeo.stats import RateInterval, pooled_rate, wilson_interval

_VALID = "o.confidence_flag <> 'unparseable'"


def _rate(cur) -> RateInterval | None:
    row = cur.fetchone()
    if row is None or row[1] in (None, 0):
        return None
    return wilson_interval(int(row[0]), int(row[1]))


def visibility(conn, store_id: int, run_id: int) -> RateInterval | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT COALESCE(SUM(o.samples_present),0), COALESCE(SUM(o.samples_total),0)
                FROM observation o JOIN prompt p ON p.id = o.prompt_id
                WHERE o.run_id = %s AND p.store_id = %s
                  AND p.type = 'product_intent' AND {_VALID}""",
            (run_id, store_id),
        )
        return _rate(cur)


def share_of_voice(conn, store_id: int, run_id: int) -> dict[str, RateInterval]:
    out: dict[str, RateInterval] = {}
    own = visibility(conn, store_id, run_id)
    out["__store__"] = own if own else wilson_interval(0, 1)
    with conn.cursor() as cur:
        cur.execute("SELECT competitors FROM store WHERE id = %s", (store_id,))
        competitors = cur.fetchone()[0]
        for comp in competitors:
            esc = comp.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cur.execute(
                f"""SELECT COALESCE(SUM(CASE WHEN EXISTS (
                          SELECT 1 FROM jsonb_array_elements_text(o.competitors_named) c
                          WHERE c ILIKE '%%' || %s || '%%' ESCAPE '\\')
                        THEN o.samples_total ELSE 0 END), 0),
                        COALESCE(SUM(o.samples_total), 0)
                    FROM observation o JOIN prompt p ON p.id = o.prompt_id
                    WHERE o.run_id = %s AND p.store_id = %s
                      AND p.type = 'product_intent' AND {_VALID}""",
                (esc, run_id, store_id),
            )
            ri = _rate(cur)
            out[comp] = ri if ri else wilson_interval(0, 1)
    return out


def engine_breakdown(conn, store_id: int, run_id: int) -> dict[str, RateInterval]:
    out = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT o.engine, o.model, SUM(o.samples_present), SUM(o.samples_total)
                FROM observation o JOIN prompt p ON p.id = o.prompt_id
                WHERE o.run_id = %s AND p.store_id = %s AND {_VALID}
                GROUP BY o.engine, o.model""",
            (run_id, store_id),
        )
        for engine, model, present, total in cur.fetchall():
            if total:
                out[f"{engine}:{model}"] = wilson_interval(int(present), int(total))
    return out


def rolling_prompt_rate(conn, prompt_id: int, engine: str, model: str,
                        last_n_runs: int = 3) -> RateInterval | None:
    """Per-prompt trends MUST pool runs (spec §5) — single-run deltas are never findings."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT o.samples_present, o.samples_total
                FROM observation o
                WHERE o.prompt_id = %s AND o.engine = %s AND o.model = %s AND {_VALID}
                  AND o.run_id IN (
                      SELECT r2.id FROM run r2
                      WHERE EXISTS (SELECT 1 FROM observation o2
                                    WHERE o2.run_id = r2.id AND o2.prompt_id = %s
                                      AND o2.engine = %s AND o2.model = %s)
                      ORDER BY r2.started_at DESC, r2.id DESC
                      LIMIT %s)""",
            (prompt_id, engine, model, prompt_id, engine, model, last_n_runs),
        )
        pairs = [(int(a), int(b)) for a, b in cur.fetchall()]
    return pooled_rate(pairs) if pairs else None
