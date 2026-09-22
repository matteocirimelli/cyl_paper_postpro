#!/usr/bin/env python3
"""Strip the initial transient from a progress_*.out file.

Keeps only the statistically stationary part of the run, i.e. the window in
which the von Karman shedding has saturated: the lift oscillates with a
constant amplitude about a constant mean and the mean drag no longer drifts.

Method
------
1. Estimate the shedding period T from mean-upcrossings of the lift signal in
   the last half of the record.
2. Slide a window of `--cycles` periods over the record; in each window compute
   the RMS of the lift about its own mean (oscillation amplitude) and the mean
   drag (level).
3. Take the reference values from the tail of the record (`--ref-frac`), then
   walk backwards and accept windows while both metrics stay within `--tol`
   of their reference.  The earliest such window marks the end of the transient.
4. Snap the cut to a lift upcrossing, and optionally trim the tail so that an
   integer number of cycles is retained (`--whole-cycles`, which makes the
   follow-up FFT nearly leakage-free).

The retained rows are written out verbatim, so the output is still a valid
progress file and can be fed straight into fft_peak.py.

Examples
--------
    ./filter_transient.py progress_Re100.out
    ./filter_transient.py progress_*.out --tol 0.02 --whole-cycles --plot
    ./filter_transient.py progress_Re150.out -o steady_Re150.out --cycles 6
"""

import argparse
import os
import sys

import numpy as np

import progress_io as pio


def sliding_metrics(t, lift, drag, width):
    """Windowed lift RMS (about the local mean) and mean drag.

    `width` is the window length in samples.  Returns the start index of each
    window along with the two metrics.  Uses cumulative sums so the cost is
    O(n) rather than O(n * width).
    """
    n = len(t)
    starts = np.arange(0, n - width + 1)

    def _win_sums(y):
        c = np.concatenate(([0.0], np.cumsum(y)))
        return c[starts + width] - c[starts]

    s1 = _win_sums(lift)
    s2 = _win_sums(lift * lift)
    mean_l = s1 / width
    var = np.maximum(s2 / width - mean_l**2, 0.0)
    rms = np.sqrt(var)
    mean_d = _win_sums(drag) / width
    return starts, rms, mean_l, mean_d


def find_transient_end(t, lift, drag, period, cycles, tol, ref_frac, min_cycles):
    """Index of the first sample belonging to the stationary regime."""
    dt = np.median(np.diff(t))
    width = max(8, int(round(cycles * period / dt)))
    if width >= len(t):
        raise SystemExit(
            f"record is only {(t[-1] - t[0]) / period:.1f} shedding periods long; "
            f"a {cycles}-period window does not fit. Lower --cycles."
        )

    starts, rms, mean_l, mean_d = sliding_metrics(t, lift, drag, width)

    # Reference = tail of the record, taken as a median so a single odd window
    # cannot move it.
    nref = max(1, int(round(len(starts) * ref_frac)))
    rms_ref = float(np.median(rms[-nref:]))
    drag_ref = float(np.median(mean_d[-nref:]))
    lift_ref = float(np.median(mean_l[-nref:]))

    if rms_ref <= 0.0:
        raise SystemExit("lift signal has no oscillation in the tail of the record")

    ok = np.abs(rms - rms_ref) <= tol * rms_ref
    ok &= np.abs(mean_d - drag_ref) <= tol * max(abs(drag_ref), 1e-30)
    # Mean lift is ~0 for a cylinder, so compare it against the oscillation
    # amplitude rather than against itself.
    ok &= np.abs(mean_l - lift_ref) <= tol * rms_ref

    if not ok[-1]:
        raise SystemExit(
            "the end of the record is not stationary by the current criteria "
            f"(tol={tol}); the run may not be converged. Try a looser --tol."
        )

    # Walk back from the end while the criteria keep holding.
    bad = np.flatnonzero(~ok)
    first_ok = int(bad[-1] + 1) if bad.size else 0
    i0 = int(starts[first_ok])

    kept_cycles = (t[-1] - t[i0]) / period
    if kept_cycles < min_cycles:
        raise SystemExit(
            f"only {kept_cycles:.1f} shedding periods would survive "
            f"(minimum {min_cycles}). The run is too short, or --tol is too tight."
        )
    return i0, dict(
        width=width, rms_ref=rms_ref, drag_ref=drag_ref, lift_ref=lift_ref,
        starts=starts, rms=rms, mean_d=mean_d, ok=ok,
    )


def upcrossings(t, y):
    """Times and indices of mean-upcrossings of y."""
    d = y - y.mean()
    up = np.flatnonzero((d[:-1] < 0) & (d[1:] >= 0))
    return up


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("files", nargs="+", help="progress_*.out file(s)")
    ap.add_argument("-o", "--output", help="output file (single input only); "
                                           "default <stem>_stat<ext>")
    ap.add_argument("--suffix", default="_stat",
                    help="suffix for auto-generated output names (default: _stat)")
    ap.add_argument("--cycles", type=float, default=4.0,
                    help="sliding-window length in shedding periods (default: 4)")
    ap.add_argument("--tol", type=float, default=0.05,
                    help="relative tolerance on amplitude and mean (default: 0.05)")
    ap.add_argument("--ref-frac", type=float, default=0.25,
                    help="tail fraction used as the converged reference (default: 0.25)")
    ap.add_argument("--min-cycles", type=float, default=4.0,
                    help="abort if fewer periods than this survive (default: 4)")
    ap.add_argument("--kind", choices=["pressure", "viscous", "total"], default="pressure",
                    help="force contribution (default: pressure; this file format "
                         "carries no viscous force)")
    ap.add_argument("--no-snap", action="store_true",
                    help="do not snap the cut to a lift upcrossing")
    ap.add_argument("--whole-cycles", action="store_true",
                    help="also trim the tail to an integer number of periods")
    ap.add_argument("--t-start", type=float, default=None,
                    help="skip the detection and cut at this physical time")
    ap.add_argument("--D", type=float, default=1.0,
                    help="cylinder diameter in metres (default: 1)")
    ap.add_argument("--ini", help="singleideal*.ini (default: auto-detected)")
    ap.add_argument("--plot", action="store_true", help="show a diagnostic plot")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    pio.add_column_args(ap)
    pio.add_force_scale_args(ap)
    args = ap.parse_args(argv)

    if args.output and len(args.files) > 1:
        ap.error("-o/--output works with a single input file")

    for path in args.files:
        pr = pio.Progress(path, pio.cols_from_args(args))
        ini_path = pio.find_ini(path, args.ini)
        ini = pio.parse_ini(ini_path) if ini_path else None
        pr.force_scale, scale_note = pio.force_scale_from_args(args, ini, args.D)
        t = pr.time
        lift = pr.force("y", args.kind)
        drag = pr.force("x", args.kind)

        period = pio.estimate_period(t, lift)
        if period is None:
            raise SystemExit(f"{path}: no lift oscillation found; "
                             "is the wake still steady?")

        if args.t_start is not None:
            i0 = int(np.searchsorted(t, args.t_start))
            diag = None
        else:
            i0, diag = find_transient_end(
                t, lift, drag, period, args.cycles, args.tol,
                args.ref_frac, args.min_cycles,
            )

        if not args.no_snap:
            up = upcrossings(t, lift)
            after = up[up >= i0]
            if after.size:
                i0 = int(after[0])

        i1 = len(t) - 1
        if args.whole_cycles:
            up = upcrossings(t, lift)
            after = up[up >= i0]
            if after.size >= 2:
                i1 = int(after[-1])

        sl = slice(i0, i1 + 1)
        kept, total = i1 + 1 - i0, len(t)
        span = t[i1] - t[i0]

        print(f"\n{path}")
        print(f"  force normalisation: {scale_note}")
        print(f"  shedding period (estimate)  T = {period:.6g}   f = {1/period:.6g}")
        if diag is not None:
            print(f"  window = {diag['width']} samples "
                  f"({args.cycles:g} periods), tol = {args.tol:g}")
            print(f"  reference  lift RMS = {diag['rms_ref']:.6g}   "
                  f"mean drag = {diag['drag_ref']:.6g}")
        print(f"  transient discarded: rows 1..{i0} "
              f"(t = {t[0]:.6g} .. {t[i0]:.6g})")
        print(f"  retained: {kept}/{total} rows, "
              f"t = {t[i0]:.6g} .. {t[i1]:.6g}  "
              f"(span {span:.6g} = {span/period:.2f} periods)")
        seg = lift[sl]
        print(f"  retained lift:  mean = {seg.mean():+.6g}  "
              f"RMS' = {seg.std():.6g}  amplitude ~ {seg.std()*np.sqrt(2):.6g}")
        print(f"  retained drag:  mean = {drag[sl].mean():.6g}  "
              f"RMS' = {drag[sl].std():.6g}")

        if not args.dry_run:
            if args.output:
                out = args.output
            else:
                stem, ext = os.path.splitext(path)
                out = f"{stem}{args.suffix}{ext}"
            with open(out, "w") as fh:
                fh.write("\n".join(pr.raw[sl]) + "\n")
            print(f"  -> wrote {out}")

        if args.plot:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
            ax[0].plot(t, lift, lw=0.6, color="0.7", label="discarded")
            ax[0].plot(t[sl], lift[sl], lw=0.6, color="C0", label="retained")
            ax[0].axvline(t[i0], color="C3", ls="--")
            ax[0].set_ylabel(f"lift F_y ({args.kind})")
            ax[0].legend(loc="upper right", fontsize=8)
            ax[0].set_title(os.path.basename(path))
            ax[1].plot(t, drag, lw=0.6, color="0.7")
            ax[1].plot(t[sl], drag[sl], lw=0.6, color="C0")
            ax[1].axvline(t[i0], color="C3", ls="--")
            if diag is not None:
                ax[1].axhline(diag["drag_ref"], color="C2", ls=":", lw=1)
            ax[1].set_ylabel(f"drag F_x ({args.kind})")
            ax[1].set_xlabel("time")
            fig.tight_layout()
            plt.show()

    return 0


if __name__ == "__main__":
    sys.exit(main())
