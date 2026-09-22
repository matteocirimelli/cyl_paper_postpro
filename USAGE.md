# How to use these scripts

Two steps: cut the transient, then FFT what's left.

```bash
python3 filter_transient.py progress_Re100.out --whole-cycles   # -> progress_Re100_stat.out
python3 fft_peak.py progress_Re100_stat.out                     # -> f, T, Re, M, St
```

Both take several files at once (`progress_*.out`), and both find the matching
`singleideal_<tag>.ini` on their own, so `uref`/`tref`/`pref`/`sutherland`/
`arearef` are picked up with no extra flags.

Needs `numpy`; `matplotlib` only for `--plot`. `progress_io.py` is the shared
reader, not something you run.

---

## 1. `filter_transient.py` — drop the start-up transient

Keeps only the part of the run where the shedding has saturated: constant lift
amplitude, constant mean drag.

**Generates** `progress_<tag>_stat.out`, the retained rows copied out verbatim
(same 17 columns, still a valid progress file). On screen it prints the
estimated period, where it cut, how many periods survived, and the mean/RMS of
lift and drag over the kept window.

| option | what it does | default |
|---|---|---|
| `-o FILE` | output name (one input file only) | `<stem>_stat.out` |
| `--suffix S` | suffix for auto-generated names | `_stat` |
| `--whole-cycles` | also trim the tail to a whole number of periods — **use it**, it makes the FFT nearly leakage-free | off |
| `--tol X` | how close to the converged value counts as stationary | `0.05` |
| `--cycles N` | sliding-window length, in shedding periods | `4` |
| `--ref-frac F` | tail fraction used as the converged reference | `0.25` |
| `--min-cycles N` | abort if fewer periods than this survive | `4` |
| `--t-start T` | skip detection, just cut at time `T` | off |
| `--no-snap` | don't snap the cut to a lift upcrossing | off |
| `--dry-run` | print the report, write nothing | off |
| `--plot` | lift and drag vs time, kept part highlighted | off |

Tighten `--tol` if you want to be stricter about convergence; it aborts with an
explanation rather than silently keeping a short record.

## 2. `fft_peak.py` — find the peak frequency

**Generates** no file by default, only the report: peak frequency, period,
amplitude, a zero-crossing cross-check, Reynolds, Mach, Strouhal, the
Williamson-Brown reference value, harmonic content, the force statistics table
and the strongest peaks. `--csv FILE` writes the spectrum, `--plot` shows
signal + spectrum.

### The force statistics table

Printed for **both** components at once, whichever one was analysed
spectrally, over a whole number of shedding cycles:

```
  FORCE STATISTICS  (reference area as above, 8 whole shedding cycles)
                                   drag  C_x       lift  C_y
    mean                           +1.037183       +0.001007
    rms about mean  (C')           +0.007898       +0.216788
    amplitude  sqrt(2)*rms         +0.011170       +0.306585
    cycle peak  max                +1.048036       +0.308928
    cycle peak  min                +1.027137       -0.305852
    cycle amplitude                +0.010449       +0.307390
    global min                     +1.023588       -0.321927
    global max                     +1.058040       +0.333148
    peak-to-peak                   +0.034452       +0.655076
    peak scatter (% of ampl.)         46.63%           3.31%
    mean drift, 1st->last cycle     -0.012431       -0.003396
```

* **cycle peak max/min** average the per-cycle extrema, which is steadier than
  a single global max/min. **global min/max** are the raw extremes.
* **cycle amplitude** = half the mean peak-to-peak. This is the quantity
  papers quote as `C_L = ±0.313` or `C_D = 1.34 ± 0.008`.
* **peak scatter** and **mean drift** are convergence checks. A converged run
  has both near zero; a large value means the mean is still moving and the
  reported mean is not yet a converged number.
* The record is binned on the *lift* period, so each bin holds one lift
  oscillation and two drag oscillations. The extrema are correct either way.

Suppress the table with `--no-force-stats`.

| option | what it does | default |
|---|---|---|
| `--signal S` | `lift` (F_y), `drag` (F_x), or `fx`/`fy`/`fz` | `lift` |
| `--no-force-stats` | skip the mean/peak force table | off |
| `--U`, `--D` | override `uref` / set the diameter | `.ini` / `1` |
| `--top N` | how many peaks to list | `3` |
| `--pad N` | zero-padding factor for sub-bin peak refinement | `8` |
| `--window W` | `hann`, `hamming`, `blackman`, `none` | `hann` |
| `--detrend D` | `mean`, `linear`, `none` | `mean` |
| `--fmin`, `--fmax` | restrict the search band | skips DC |
| `--t-start`, `--t-end` | crop in time (crude alternative to step 1) | off |
| `--csv FILE` | write frequency,amplitude | off |
| `--plot` | time signal and spectrum | off |
| `--no-ini` | don't read any `.ini`; raw frequency only | off |

Lift oscillates at `f_s`, drag at `2 f_s` — the printed harmonic ratios tell
you which you measured.

---

## Shared options

### Force normalisation

The solver writes `F / (qref * arearef)` with `arearef = 0.785` from the
`.ini`. Both scripts undo that on read:

    value_used = column * arearef / (D * span)

| option | what it does | default |
|---|---|---|
| `--D d` | cylinder diameter | `1` |
| `--span s` | spanwise length included in the reference area | `1` |
| `--ref-area A` | set the target area directly, ignoring `D`/`--span` | `D * span` |
| `--arearef A` | what the solver divided by | from the `.ini` |
| `--raw-forces` | leave the numbers exactly as written | off |

Defaults give reference area = `D` = 1, i.e. a factor of 0.785. Use
`--span 0.8944272` for the standard 2D coefficient referred to `D * span`.
**None of this moves the frequency or the Strouhal number** — only the printed
force levels.

### Column overrides

Defaults match these files (`fp_x`,`fp_y`,`fp_z` = 6,7,8). Override any of them
with `--col-fp-y 7`, `--col-time 3`, ... (1-based). The viscous forces are
unset because this file format doesn't contain them, so `--kind viscous` and
`--kind total` refuse to run unless you supply `--col-fv-x` etc.; `--kind`
defaults to `pressure`.

---

## Worked example

```bash
# both cases, whole number of cycles, then the spectra
python3 filter_transient.py progress_Re100.out progress_Re150.out --whole-cycles
python3 fft_peak.py progress_Re*_stat.out --D 1.0

# coefficients referred to D*span instead, with a plot and the spectrum on disk
python3 fft_peak.py progress_Re100_stat.out --span 0.8944272 --plot --csv spec.csv

# skip the filter and cut by hand
python3 fft_peak.py progress_Re150.out --t-start 1.0
```

Current results: `St = 0.1702` at `Re = 101.7`, `St = 0.1884` at `Re = 152.5`.

See `README.md` for the column layout, how it was verified against the solver
source, and the method behind each step.
