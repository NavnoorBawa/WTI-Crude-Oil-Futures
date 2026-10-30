# Pre-registration: the live promotion test

This file states, in one citable place, the only test that can replace the model of record: what it
compares, on which sample, with which statistic and level, and what happens after it. The rule
itself lives in code (`backend/vol_record.py`). The rule's text, the design table, the scoring
window, the earliest completion date, the backtest's dependence figures, the power and sections 7
and 9 are generated from that code and from `data/vol_lab.json` by
[cog](https://nedbatchelder.com/code/cog) (`scripts/readme_blocks.py`); the values and names the
hand-written sections quote are checked against the code by `tests/test_preregistration.py`. CI
fails if either disagrees. The file was written on 2026-10-30; the rule it describes was fixed on
2026-10-21 and has not changed.

## 1. Registration

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("promotion_rule") ]]] -->

> The promotion test is pinned to one pair: HAR-IV v2 (model version har-iv/2), the model of record, against the HAR-IV-range challenger (key har_iv_range). The challenger replaces the model of record if, on the first 52 independent resolved weeks where both were forecast, its mean QLIKE is lower with a one-sided Diebold-Mariano p below 0.10 (Harvey-Leybourne-Newbold, Student t with 51 degrees of freedom). The test is run once, on that fixed sample. Forecasts from another model version or challenger are recorded but never enter it; a different challenger would start a new, separately declared test. A maintainer applies the verdict.
<!-- [[[end]]] -->

- **Fixed in:** commit `48138bc` ("2.0.0", pull request #54). GitHub records the merge at
  2026-10-21 05:02:56 UTC.
- **Before any live evidence:** the first record entry (the forecast as of 2026-10-20) was made by
  Refresh run [39547366346](https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/actions/runs/39547366346),
  which GitHub started for the merge commit itself at 05:02:59 UTC and which finished at 05:04:25
  UTC. The entry's own `sealed_at` is 05:04:20 UTC and its push to `live-data` is commit `1562c29`.
  The first window resolved on 2026-10-29.
- **Unchanged since:** the four constants that define the test (`PROMOTION_MODEL_VERSION`,
  `PROMOTION_CHALLENGER`, `PROMOTION_WEEKS`, `PROMOTION_ALPHA`) have one commit in their history,
  and `tests/test_vol_record.py` fails if the rule's text, which names all four, changes.

A git commit's date is set by its author, so the times that count are GitHub's: the merge time of
pull request #54 and the Actions run that made and pushed the first entry. A run started for the
merge commit cannot predate the merge. GitHub keeps run logs and push events for a limited period,
not indefinitely; an independent timestamp anchor ([`docs/ROADMAP.md`](ROADMAP.md), Stage 1, item 3)
would make the order checkable for good. To check them:

```bash
git log --format='%h %cI %s' -G '^PROMOTION_(MODEL_VERSION|CHALLENGER|WEEKS|ALPHA) = ' -- backend/vol_record.py
```

```bash
curl -s https://api.github.com/repos/NavnoorBawa/WTI-Crude-Oil-Futures/pulls/54 | grep '"merged_at"'
```

```bash
curl -s 'https://api.github.com/repos/NavnoorBawa/WTI-Crude-Oil-Futures/actions/runs/39547366346' | grep -E '"(name|head_sha|created_at|updated_at)"'
```

```bash
git show origin/live-data:runtime-state/vol_track_record.json | python scripts/verify_record.py -
```

The last command re-derives every entry from the record file alone, including how long before its
scoring window each entry was sealed ([`docs/REVIEWER_GUIDE.md`](REVIEWER_GUIDE.md)).

## 2. Hypotheses

Let d be the QLIKE loss of the model of record minus that of the challenger on one live forecast,
so a positive d favours the challenger.

- **H0:** on live forecasts, the challenger's expected QLIKE is not lower than the model of
  record's (E[d] ≤ 0).
- **H1:** it is lower (E[d] > 0).

The test is one-sided because only one outcome changes anything: replacing the model of record.

## 3. Design

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("prereg_design") ]]] -->

| Element | Registered value | Fixed in |
| --- | --- | --- |
| Model of record | HAR-IV, model version `har-iv/2` | `vol_record.PROMOTION_MODEL_VERSION` |
| Challenger | HAR-IV-range, key `har_iv_range` | `vol_record.PROMOTION_CHALLENGER` |
| Loss | QLIKE on variances: r − ln r − 1, with r the realized over the forecast variance | `vol_record.qlike` |
| Loss difference | d = QLIKE(model of record) − QLIKE(challenger); positive favours the challenger | `vol_record.promotion_test` |
| Unit | one recorded forecast, scored on the realized volatility of the next 5 settlement-to-settlement returns | `vol_record.HORIZON_SESSIONS` |
| Sample | the first 52 resolved entries of the pair whose windows do not overlap, in record order | `vol_record.PROMOTION_WEEKS`, `promotion_sample` |
| Statistic | mean of d over its standard error (sample variance, n − 1 denominator, no autocovariance terms); one-sided p from Student's t with 51 degrees of freedom | `vol_record.promotion_test`, `student_t_sf` |
| Level | 0.10, one-sided: promote when the statistic exceeds t(51)'s upper 10% point, 1.2984 | `vol_record.PROMOTION_ALPHA` |
| Looks | one, when the sample is full; later entries never change the verdict | `vol_record.promotion_sample` |

The Student t reference matters at this sample size: the standard normal's upper 10% point is 1.2816, and rejecting above it would have a size of 10.3% under t(51), not 10%.
<!-- [[[end]]] -->

Each forecast's window is fixed when it is recorded:

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("prereg_window") ]]] -->

> Each forecast is scored on the realized volatility of the five settlement-to-settlement returns after the first CL settlement (14:30 ET) at least an hour after it was issued and recorded, one session or more later than the backtest's window. The record is sealed just before each git commit; an entry not sealed more than five minutes before that settlement is marked late_commit and never scored, so every scored forecast was committed before any price it is scored on.
<!-- [[[end]]] -->

## 4. Sample and stopping rule

- **Which entries count.** The record holds one entry per session; the first forecast recorded for
  a session stands. Only resolved entries of the pinned pair are candidates: the model of record at
  version `har-iv/2` with a challenger forecast from `har_iv_range`.
- **Independence.** Daily forecasts of five-session windows overlap. In record order, an entry is
  taken only if its window starts after the window of the last entry taken (greedy), so the sample's
  windows never overlap. The statistic then treats the selected weeks as serially uncorrelated
  (section 5).
- **Stopping.** The scan stops at the first entry of the pair that is still pending, because it may
  still resolve and its window comes before those of later entries. The sample is the first 52
  entries taken. It only grows, and once full it never changes, so there is one look and no
  optional stopping.
- **Kept out, by rules fixed in code:** an entry whose window spans a front-month roll
  (`skipped_roll`, it would measure the calendar spread), an entry whose closes are still missing
  more than 10 sessions after its window ends (`expired`, `vol_record.MAX_WAIT_SESSIONS`), and an
  entry not sealed in time (`late_commit`) are never scored. An entry of another model version
  (such as the plain-HAR forecast published when no lagged OVX print is available), with another
  challenger or with no challenger forecast is recorded and never enters the sample, so it can
  neither hold the sample back nor restart it; once resolved, it is counted by reason
  (`promotion.n_excluded_*` in the record summary).

How long the sample takes, under the record's own rules:

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("prereg_duration") ]]] -->

At the earliest, the sample is full when the window ending 2028-01-05 resolves: 303 sessions after the first window opened on 2026-10-22 (about 14 months), not 52 × 5 = 260. That is the case in which every session from the first entry on is recorded, sealed in time and paired; windows that cross the monthly contract roll are never scored, so every roll costs the greedy selection one to five sessions. Each missed, unpaired or expired session delays it further. The record summary publishes `n_independent_paired` against `required` as the sample accrues.
<!-- [[[end]]] -->

## 5. Analysis and decision

On the full sample, compute the mean of d, its standard error from the sample variance with an
n − 1 denominator, and their ratio. The statistic has no autocovariance terms: it treats the
loss differences of non-overlapping weeks as serially uncorrelated. For one-step forecasts it
equals Harvey, Leybourne and Newbold's (1997) corrected Diebold-Mariano statistic, and, as they
recommend, its p-value is from Student's t with n − 1 = 51 degrees of freedom
(`vol_record.student_t_sf`, a closed-form series, standard library only).

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("prereg_dependence") ]]] -->

That is an assumption, and the backtest does not fully support it. On its weekly series (927 non-overlapping weeks, 2008–2026, forecasts rounded to 0.1 vol points), the same loss difference d has a lag-1 autocorrelation of 0.19, and its Newey-West long-run variance (6 lags) is 1.36 times the plain variance. If live weeks behave alike, the plain standard error understates the uncertainty, and the test's actual size need not equal its nominal 10%. The rule was fixed with this statistic and is applied as registered; the backtest's own Diebold-Mariano tests (sections 6 and 7) use Newey-West standard errors.
<!-- [[[end]]] -->

- **promote** if the mean of d is positive and the p-value is below 0.10;
- **keep** otherwise, including when the sample variance of d is exactly zero (no statistic can be
  formed);
- **accruing** until the sample is full.

The record summary publishes the status, the statistic, its p-value and the mean difference
(`promotion` in `vol_forecast.live_record`, [`docs/DATA_CONTRACT.md`](DATA_CONTRACT.md)). The LIVE
panel shows the count while the sample accrues, then the verdict with its statistic and p-value.

## 6. Power

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("promotion_power") ]]] -->

How much the single test can tell: on the five sampling grids the challenger's Diebold-Mariano statistic against the model of record has a median of 2.6, each over 926 or 927 non-overlapping weeks. That edge was measured on the sample that selected the challenger, so it is more likely to overstate the true edge than to understate it, and the power below is correspondingly optimistic. If that is its true edge, the test on 52 independent weeks promotes it with probability 25%, and a fixed-sample test with 80% power would need about 619 weeks (12 years). This treats the weekly loss differences as normal, so the statistic is noncentral t with 51 degrees of freedom, compared with the test's own critical value, t(51)'s 1.298. If the challenger is no better, the test promotes it with probability at most 10%, its size. With 25% power, a keep verdict on that sample does not show that the challenger is no better than the model of record.
<!-- [[[end]]] -->

## 7. What was known when the rule was fixed

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("prereg_selection") ]]] -->

The challenger was not chosen blind. On the historical sample it has the lowest QLIKE of the 12 alternatives to the model of record (0.353 against 0.370; pairwise Diebold-Mariano p = 0.002), and it was picked for that reason, so its pairwise p-value overstates the evidence. The test that accounts for the search is Hansen's SPA over all 12 alternatives. When the rule was fixed it was published as p = 0.072; that value was the non-studentized test (the 2.2.0 errata in CHANGELOG.md). Studentized as Hansen defines it, the test gives p = 0.011 (significant at 5%), though the rejection does not survive the ledger's Benjamini-Hochberg adjustment across the 75 hypotheses in its multiplicity family (q = 0.090). None of these numbers enters the live test. They are why the challenger runs live instead of replacing the model of record, and why only forecasts recorded after the rule was fixed decide.
<!-- [[[end]]] -->

## 8. What happens next

- **Keep.** The model of record stays, and the record goes on. A keep is final for this pair:
  the sample is the first 52 weeks, no later look changes the verdict, and this registration
  provides no second test of the same pair. A test of another pair is a new test: it is declared by
  new constants in a commit made before its first entry, and it gets its own pre-registration.
- **Promote.** A maintainer's pull request makes the challenger the model of record in code. The
  record keeps every entry as sealed; nothing recorded is changed or deleted.
- **Either way,** there are no interim decisions: until the sample is full, the status is
  `accruing` and nothing about the pair changes because of the live results.

## 9. Deviations

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("prereg_deviations") ]]] -->

None to the test. Since the rule was fixed, one backtest result it does not use was corrected: in 2.2.0 the SPA p-value was published studentized (p = 0.011) in place of the non-studentized 0.072 (section 7). The test's pair, sample, statistic and level are unchanged.
<!-- [[[end]]] -->

## 10. Citation

Cite the repository at the commit that fixed the rule (`48138bc`) together with this file, using
[`CITATION.cff`](../CITATION.cff). A DOI for each release needs the repository owner to enable the
Zenodo-GitHub integration ([`docs/ROADMAP.md`](ROADMAP.md), Stage 1, item 2); this section will
name it once one exists.

## References

- Diebold, F. X., and Mariano, R. S. (1995). Comparing predictive accuracy. *Journal of Business &
  Economic Statistics* 13(3), 253–263.
- Hansen, P. R. (2005). A test for superior predictive ability. *Journal of Business & Economic
  Statistics* 23(4), 365–380.
- Harvey, D., Leybourne, S., and Newbold, P. (1997). Testing the equality of prediction mean squared
  errors. *International Journal of Forecasting* 13(2), 281–291.
- Nosek, B. A., Ebersole, C. R., DeHaven, A. C., and Mellor, D. T. (2018). The preregistration
  revolution. *PNAS* 115(11), 2600–2606.
- Patton, A. J. (2011). Volatility forecast comparison using imperfect volatility proxies. *Journal
  of Econometrics* 160(1), 246–256.
