# Hawkes process

Fit and diagnose self-exciting point processes on event times, especially **OKX crypto order flow** (trade arrivals, buy/sell cross-excitation). Use this skill when the question is about clustering of trades, branching ratios, or mutually exciting buy vs sell intensity.

Do **not** paste book chapters into the repo or into user-facing notes. The reference is Laub, Lee, and Taimre, *The Elements of Hawkes Processes* (Springer, 2021). Cite chapter and equation numbers. Implementation: `tools/hawkes/` (corrected rewrite of the book's sample code; see that module header for the four sample-code defects).

Default fitter is the **O(N) exponential MLE**. Do not run textbook EM on large N.

## When to use

- Trade-time (or quote-time) point process on `[0, T]`, possibly marked buy/sell.
- You need a stationary intensity, branching ratio, residual QQ/KS, or a comparison of exponential vs power-law kernels.
- Skip this model if the object is a calendar-time return series with no event timestamps, or if you only need a Poisson baseline.

## Model

Linear Hawkes intensity (Definition 3.1, eq. 3.2):

$$
\lambda^*(t)=\lambda+\sum_{t_i<t}\mu(t-t_i),\qquad \lambda>0,\ \mu:(0,\infty)\to[0,\infty).
$$

**Exponential kernel** (eq. 3.3): $\mu(t)=\alpha e^{-\beta t}$, $\alpha,\beta>0$. Jump $\alpha$, decay $\beta$. Compensator (eq. 5.6):

$$
\Lambda(t)=\lambda t+\sum_{t_i<t}\frac{\alpha}{\beta}\bigl(1-e^{-\beta(t-t_i)}\bigr).
$$

**Power-law kernel** (eq. 3.4, Omori-style): $\mu(t)=k(c+t)^{-p}$ with $k,c>0$, $p>1$.

$$
\int_0^{\Delta}\mu(s)\,ds=\frac{k}{p-1}\Bigl[c^{1-p}-(c+\Delta)^{1-p}\Bigr].
$$

**Multivariate / mutually exciting** (Definition 3.3). For OKX, $m=2$ with marks $0=\mathrm{buy}$, $1=\mathrm{sell}$.

**Index convention (ch. 3 vs ch. 5 — they disagree):**

- Chapter 3 writes $\alpha_{i,j}$ as the jump in process **i** (target) from an event of process **j** (source).
- Chapter 5 and `tools/hawkes` store `A` with **`A[source, target]`**. An event of type $d$ adds the row `A[d, :]` to the intensity vector. Intensity $k$ is

$$
\lambda_k^*(t)=\lambda_k+\sum_{t_i<t} A_{d_i,k}\,e^{-\beta_k(t-t_i)}.
$$

Always say which convention you used. Do not mix a ch. 3 matrix with this toolkit.

## Stationarity

Univariate branching ratio (Theorem 3.1): $n=\int_0^\infty\mu(s)\,ds$. Exponential: $n=\alpha/\beta$. Power-law: $n=k/((p-1)c^{p-1})$. Need **$n<1$**. Then $\mathbb{E}[\lambda^*(t)]\to\lambda/(1-n)$ and $N(t)$ is asymptotically normal. If $n\ge 1$ the process explodes.

Multivariate: branching matrix $\Phi$ with $\Phi_{k,j}=A_{j,k}/\beta_k$ (expected type-$k$ offspring of one type-$j$ event, ch. 5 indexing). Stationary iff the **spectral radius** $\rho(\Phi)<1$. Near $\rho=1$ (or $n\approx 1$) estimates of $\alpha,\beta$ are poorly identified; report that and do not treat point estimates as sharp.

## Estimation (what to run)

| Method | Use when | Cost / caveat |
| --- | --- | --- |
| **MLE (default)** `exp_mle` / `exp_mle_with_grad` | Fully observed event times | $O(N)$ for exponential (Ozaki recursion, eqs. 5.7–5.8). Matches `hawkeslib` likelihood when that package is available; do not depend on `hawkeslib` at runtime (its sampler uses removed `np.float`). |
| MLE + Hessian | Need intervals | `asymptotic_se` uses observed information $-\nabla^2\ell$. Quote $\hat\theta\pm 1.96\,\mathrm{se}$. Check that $-\nabla^2\ell$ is positive definite. |
| Power-law MLE `power_mle` | Long memory / aftershock-like decay | Likelihood is $O(N^2)$. Compare to exponential on the same window. |
| GMM `exp_gmm` | Interval-censored counts, or a check on MLE | Eqs. 7.11–7.13. Weight matrix is $\hat V^{-1}$ from **batch means** of the moment vector (counts are autocorrelated). Do **not** use $W=(gg^\top)^{-1}$ (rank 1). |
| EM `exp_em` | Small $N$, branching interpretation | Textbook EM is $O(N^2)$ time (ch. 6). This toolkit refuses $N>2000$ unless `allow_large=True`. Prefer MLE. |
| Bayesian (ch. 8) | Priors, full posterior, $n$ near 1 | Metropolis–Hastings on $(\lambda,\alpha,\beta)$ with the exponential log-likelihood as $p(\mathrm{data}\mid\theta)$. Not implemented as a sampler here; keep chains short and report posterior intervals, not only the MAP. |

Out-of-sample: fit on a training window, evaluate log-likelihood and residual KS on a later window with **no overlap**. Do not tune $\tau$, lag, or kernel on the test window.

## Goodness of fit (ch. 9)

Random time-change (Theorem 9.2): $t_i^*=\Lambda(t_i)$ is unit-rate Poisson iff $\Lambda$ is the true compensator. Residuals $\Delta\Lambda_i=\Lambda(t_i)-\Lambda(t_{i-1})$ should be i.i.d. Exp(1).

1. `time_rescaled_residuals` then QQ against Exp(1).
2. `ks_exponential_residuals` (KS). Treat a pass as necessary, not sufficient.
3. If residuals fail, try power-law or a sum of exponentials before adding marks.

## Practical pitfalls (market data / OKX)

**Tied timestamps.** Matching engines batch at a tick (often 1 ms). A simple Hawkes process cannot have ties.

- Prefer **aggregating** coincident prints at the same timestamp (one event, optional size mark) over jitter.
- Uniform jitter of size equal to the tick **biases $\alpha$ and $\beta$**. Lorenzen (and related microstructure work) show that spreading ties looks like extra excitation plus faster decay. If you jitter, say so and treat kernel parameters as contaminated.
- The finance chapter of the reference jittered stock trades; do not copy that as best practice.

**Non-stationary baseline.** $\lambda$ is not constant across sessions, weekends, or event windows.

- Crypto venues such as OKX run 24/7; still split or dummy **UTC hours**, weekends, and US/Asia cash-session overlaps.
- For traditional-market holidays and early closes that move overlapping equity/futures flow (FOMC, BoJ, cash-session holidays), read **only** `load_confirmed()` from `tools.exchange_calendar`. Never read `data/exchange_calendar/holidays.csv` directly. Use `official` and `secondary-confirmed` rows only. CME/KRX unverified rows are not confirmed.
- Drop or separately dummy FOMC / BoJ / CPI windows rather than forcing a constant $\lambda$ through them.
- If `last_refreshed` is older than 35 days, say the calendar file is stale before relying on upcoming dates.

**Kernel misspecification.** Exponential is a memoryless cluster. Order-flow residuals often want slower decay. Fit exponential and power-law (or a sum of two exponentials) on the same sample; keep the one with better OOS likelihood and residual KS. Do not claim a unique kernel from in-sample $\ell$ alone.

**Branching ratio near 1.** $\hat n\to 1$ usually means a slow baseline, a missing mark, or a non-stationary window, not “infinite contagion”. Shorten the window, add a piecewise $\lambda$, or stop.

**Confidence intervals.** Report Hessian-based SEs for MLE. GMM SEs come from the sandwich with the same $\hat V$ used as the weight. Simulation envelopes are not a substitute for SEs.

## OKX order-flow recipe

1. Build a simple point process: trade times in seconds from a session start, marks buy/sell from aggressor side. Resolve ties (aggregate).
2. Check stationarity of counts per minute; if the session has a U-shape, split or use a piecewise baseline.
3. Univariate exponential MLE on all trades as a baseline Poisson alternative ($n=0$).
4. Bivariate MLE with `A[source, target]` for buy $\leftrightarrow$ sell. Quote $\rho(\Phi)$, the four branching entries, and Hessian SEs if the Hessian inverts.
5. Residual KS in-sample and on the next session. If KS fails, try power-law or drop event windows via `load_confirmed()` plus known macro times.
6. Never ship a model with $n\ge 1$ or $\rho(\Phi)\ge 1$ as if it were stationary.

## Code

```python
from tools.hawkes import (
    exp_mle_with_grad,
    asymptotic_se,
    simulate_exp_by_thinning,
    time_rescaled_residuals,
    ks_exponential_residuals,
    branching_ratio_exp,
)

theta_hat = exp_mle_with_grad(times, T)
se = asymptotic_se(times, T, theta_hat)
n = branching_ratio_exp(theta_hat)
residuals = time_rescaled_residuals(times, T, theta_hat)
stat, pvalue = ks_exponential_residuals(residuals)
```

```bash
python3 -m pytest tools/hawkes/tests -q
```

Dependencies: `numpy`, `scipy`. `numba` is optional (JIT of hot loops; pure NumPy fallback). Do not import `hawkeslib` in production code.

## Do not

- Do not commit the book's markdown or its original `hawkes.py`.
- Do not use the sample-code variance $\beta/\kappa$ (eq. 7.12 is $\beta^2/\kappa^2$).
- Do not use autocovariance gap `lag * tau` when counting bins `lag` apart (eq. 7.13 $\delta=(lag-1)\tau$).
- Do not invert $gg^\top$ as a GMM weight.
- Do not treat jittered timestamps as unbiased kernel evidence.
- Do not read holiday CSVs directly; use `load_confirmed()`.
