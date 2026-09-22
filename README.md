# progress_*.out post-processing

Two tools for extracting the von Karman shedding frequency from the solver's
`progress_*.out` files.

    ./filter_transient.py progress_Re100.out --whole-cycles   # -> progress_Re100_stat.out
    ./fft_peak.py progress_Re100_stat.out --U <Uinf> --D <D>  # -> f, T, St

`progress_io.py` is the shared reader; the two scripts are the entry points.
Requires numpy (matplotlib only for `--plot`).

## Column layout

Confirmed against the solver source, `subroutine print_progress` in
`streams_3/code_unstructured/src/singleideal/singleideal_gpu.F90` (laminar
branch, i.e. `rans_model < 0` and `les_model == 0`):

| col | quantity | col | quantity |
|----:|----------|----:|----------|
| 1 | iteration      | 6-8   | pressure force x, y, z |
| 2 | dt             | 9-10  | rho min, max |
| 3 | time           | 11-12 | T min, max |
| 4 | residual rho*u | 13-14 | p min, max |
| 5 | residual rho*e | | |

That is 14 columns; the files actually have 17. The trailing three come from a
locally modified build that matches no source variant on this machine.

**Column 15 is not the viscous drag.** It oscillates at `f_s` with zero mean,
whereas viscous drag must oscillate at `2 f_s` about a positive mean. Columns
16-17 are O(1e-17), i.e. numerically zero. The viscous forces are simply not
in this file, so `--kind viscous` / `--kind total` refuse to run unless you say
where they live (`--col-fv-x N`). The default is `--kind pressure`.

## The forces are already non-dimensional (and rescaled on read)

`compute_force` divides by `qref*arearef` before writing:

    qref = 0.5 * rhoref * uref**2          arearef  from the .ini  = 0.785

so columns 6-8 are force *coefficients*, not newtons. (Cross-check: the mean of
column 6 changes by only 3.9% between the two runs, not by the factor
`(57/38)^2 = 2.25` that a dimensional force would show.)

`arearef = 0.785` (= pi/4, the cylinder *cross-section*) is not the reference
area we want, so both scripts undo it on read:

    value_used = column * arearef / (D * span)

Flags, shared by both scripts:

| flag | meaning | default |
|---|---|---|
| `--arearef A` | what the solver divided by | read from the `.ini` (0.785) |
| `--D d` | cylinder diameter | 1 |
| `--span s` | spanwise length in the reference area | 1 |
| `--ref-area A` | set the target area directly, ignoring `D`/`--span` | `D * span` |
| `--raw-forces` | leave the numbers exactly as written | off |

The default is the requested convention: reference area = `D` = 1, i.e. a
factor of **0.785**. Frequencies and Strouhal numbers are unaffected by any of
this -- only the printed force levels change.

### Caveat on the span

The solver sums forces over the real cylinder faces, so the written force is
the load on the whole spanwise extent, not a load per unit span. From the patch
areas in `out.log` that extent is

    span = 17.88854382 / 20 = 0.8944272 m      (not 1)

so the standard 2D coefficient `Cd = (F/span)/(qref*D)` needs
`--span 0.8944272` (factor 0.877657). With `--span 1` the leftover span factor
of 0.894 is still in the numbers. Both are one flag apart:

| normalisation | factor | mean C_x, Re 101.7 | mean C_x, Re 152.5 |
|---|---|---|---|
| `--raw-forces` (arearef 0.785) | 1.0 | 1.1818 | 1.2273 |
| default, area = D = 1 | 0.785 | 0.9277 | 0.9634 |
| `--span 0.8944272`, area = D*span | 0.877657 | 1.0372 | 1.0772 |

## Reference state

Read automatically from `singleideal_<tag>.ini` sitting next to the progress
file (or `--ini`). `rho = pref/(rgas*tref)` and the Sutherland law

    mu(T) = mu0 * (T/T0)^1.5 * (T0+S)/(T+S)

with `sutherland = 0.4085, 273.15, 110.4` reproduce the `Mixture density` and
`Mixture viscosity` printed in `out.log` to machine precision. The two cases
differ only in `uref` (38 vs 57 m/s), giving `Re = 101.7` / `152.5` and
`M = 0.109` / `0.164`.

## filter_transient.py

Drops the initial transient and keeps the saturated shedding regime.

1. Estimates the shedding period `T` from lift mean-upcrossings in the last
   half of the record.
2. Slides a window of `--cycles` periods (default 4) and computes, per window,
   the lift RMS about its own mean (the oscillation amplitude) and the mean drag.
3. Takes reference values from the tail (`--ref-frac`, default 0.25), walks
   backwards, and accepts windows while both stay within `--tol` (default 5%)
   of the reference. The earliest such window ends the transient.
4. Snaps the cut to a lift upcrossing; `--whole-cycles` also trims the tail to an
   integer number of periods, which makes the follow-up FFT nearly leakage-free.

Rows are copied out verbatim, so the result is still a valid progress file.

Useful flags: `--tol`, `--cycles`, `--min-cycles`, `--kind pressure|viscous|total`,
`--t-start <t>` (skip detection, cut manually), `--dry-run`, `--plot`.

## fft_peak.py

1. Resamples onto a uniform time grid -- the solver's `dt` drifts ~2% over a run,
   so samples equispaced in *iteration* are not equispaced in *time*.
2. Removes the mean, applies a Hann window, zero-pads (`--pad`, default 8x).
3. rFFT, then refines the peak bin by parabolic interpolation of the
   log-magnitude, giving a frequency much finer than the bin spacing. This
   matters: these records are only ~8-13 periods long, so the unpadded bin
   spacing (~0.7) is ~10% of the frequency being measured.
4. Cross-checks against a plain zero-crossing count and prints both. If they
   disagree by more than ~1% the signal is not a clean single tone -- look at
   `--plot` before trusting the number.

Amplitudes are coherent-gain corrected, so a pure tone reads as its true
amplitude and can be compared against the printed `sqrt(2)*RMS'`.

It then prints a force statistics table for both components: mean, RMS about
the mean (`C'`), `sqrt(2)*RMS`, the mean per-cycle peaks, the cycle amplitude
(half the mean peak-to-peak -- the `C_L = ±0.313` convention used in the
literature), the global extremes, and two convergence checks (scatter of the
per-cycle peaks, and how far the cycle mean moved from the first retained cycle
to the last). `--no-force-stats` turns it off. See `VALIDATION.md` for what
these can and cannot be compared against.

Useful flags: `--signal lift|drag`, `--U`/`--D` (Strouhal), `--top N`,
`--fmin`/`--fmax`, `--window`, `--csv`, `--plot`.

Note the lift `F_y` oscillates at `f_s` while the drag `F_x` oscillates at
`2 f_s`; the printed harmonic ratios let you confirm which one you measured.
