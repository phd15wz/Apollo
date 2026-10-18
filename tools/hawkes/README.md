# Hawkes process toolkit

Simulation, O(N) exponential MLE, moments/GMM, and residual checks for linear Hawkes processes. Used by `skills/hawkes-process/SKILL.md`.

This is an original, corrected rewrite of ideas in the sample code for Laub, Lee, and Taimre, *The Elements of Hawkes Processes* (Springer, 2021). The book's chapters and original `hawkes.py` are not in this repository.

## Run

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest tools/hawkes/tests -q
```

`numpy` and `scipy` are required. `numba` is optional.

## Layout

`hawkes.py` is the toolkit. Tests in `tests/` lock the four sample-code fixes (variance 7.12, autocovariance gap, inverse-compensator history, GMM weight rank), plus score vs finite differences, MLE recovery, thinning rate $\lambda/(1-n)$, and power-law compensator vs quadrature.
