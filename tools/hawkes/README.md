# Hawkes process toolkit

Independent implementation of linear Hawkes formulas from Laub, Lee, and Taimre, *The Elements of Hawkes Processes* (Springer, 2021). Used by `skills/hawkes-process/SKILL.md`. The book's text and any sample programs that shipped with it are not in this repository.

Layout: `kernels.py`, `likelihood.py`, `simulate.py`, `moments.py`, `estimate.py`, `diagnostics.py`, `multivariate.py`.

## Run

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest tools/hawkes/tests -q
```

`numpy` and `scipy` are required.

## Tests

`tests/` checks eq. 7.12 variance, eq. 7.13 bin gap, time-change history prefix, GMM batch-means weights, score vs finite differences, MLE recovery, thinning rate $\lambda/(1-n)$, power-law compensator vs quadrature, EM/power-law size caps, and non-stationary guards.
