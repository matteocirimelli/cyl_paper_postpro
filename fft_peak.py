#!/usr/bin/env python3
"""Find the peak (vortex-shedding) frequency in a progress_*.out file by FFT.

Intended to be run on the output of filter_transient.py, i.e. on a record that
already contains only the saturated von Karman regime.  Feeding it a file that
still contains the transient will smear the peak.

What it does
------------
* resamples onto a uniform time grid (the solver's dt drifts by ~2% over a run,
  so samples equispaced in iteration are not equispaced in time);
* removes the mean (or a linear trend);
* applies a window (Hann by default) to suppress leakage;
* zero-pads and takes an rFFT;
* locates the peak bin and refines it by parabolic interpolation of the
  log-magnitude, which gives a frequency far finer than the bin spacing;
* cross-checks the result against a straight zero-crossing count.

The lift F_y oscillates at the shedding frequency f_s; the drag F_x oscillates
at 2*f_s.  The script reports the harmonic ratio so you can tell which you got.

Examples
--------
    ./fft_peak.py progress_Re100_stat.out
    ./fft_peak.py progress_Re100_stat.out --D 1.0               # -> Re, M, St
    ./fft_peak.py progress_Re150_stat.out --signal drag --plot
    ./fft_peak.py progress_Re100.out --t-start 2.3 --csv spec.csv
"""

import argparse
import os
import sys

import numpy as np

import progress_io as pio

WINDOWS = {
    "none": lambda n: np.ones(n),
    "hann": np.hanning,
    "hamming": np.hamming,
    "blackman": np.blackman,
}


def parabolic_peak(mag, k):
    """Sub-bin peak location and height from a parabola through log-magnitude.

    Returns (offset in bins, interpolated magnitude).  The offset is in
    [-0.5, 0.5]; falls back to the bare bin at the array edges.
    """
    if k <= 0 or k >= len(mag) - 1:
        return 0.0, mag[k]
    eps = 1e-300
    a, b, c = (np.log(max(mag[k + d], eps)) for d in (-1, 0, 1))
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return 0.0, mag[k]
    delta = 0.5 * (a - c) / denom
    delta = float(np.clip(delta, -0.5, 0.5))
    peak_log = b - 0.25 * (a - c) * delta
    return delta, float(np.exp(peak_log))


def zero_crossing_frequency(t, y):
    """Frequency from evenly-counted mean-upcrossings; independent of the FFT."""
    d = y - y.mean()
    up = np.flatnonzero((d[:-1] < 0) & (d[1:] >= 0))
    if up.size < 2:
        return None
    frac = -d[up] / (d[up + 1] - d[up])
    tc = t[up] + frac * (t[up + 1] - t[up])
    return float((len(tc) - 1) / (tc[-1] - tc[0]))


def spectrum(t, y, window="hann", pad=8, detrend="mean"):
    """Single-sided amplitude spectrum of y(t). Returns (freq, amplitude, dt)."""
    tu, yu, dt = pio.uniform_resample(t, y)
    n = len(yu)
    if n < 16:
        raise SystemExit("too few samples for an FFT")

    if detrend == "linear":
        yu = yu - np.polyval(np.polyfit(tu, yu, 1), tu)
    elif detrend == "mean":
        yu = yu - yu.mean()

    w = WINDOWS[window](n)
    yw = yu * w
    nfft = int(2 ** np.ceil(np.log2(n * max(1, pad))))
    spec = np.fft.rfft(yw, nfft)
    freq = np.fft.rfftfreq(nfft, dt)
    # Coherent-gain correction, so a pure tone reads as its true amplitude.
    amp = 2.0 * np.abs(spec) / np.sum(w)
    return freq, amp, dt, n, nfft


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("files", nargs="+", help="progress_*.out file(s), ideally filtered")
    ap.add_argument("--signal", default="lift",
                    choices=["lift", "drag", "fx", "fy", "fz"],
                    help="signal to analyse (lift=F_y, drag=F_x; default: lift)")
    ap.add_argument("--kind", choices=["pressure", "viscous", "total"], default="pressure",
                    help="force contribution (default: pressure; this file format "
                         "carries no viscous force)")
    ap.add_argument("--t-start", type=float, default=None,
                    help="ignore samples before this time (crude transient cut)")
    ap.add_argument("--t-end", type=float, default=None, help="ignore samples after this time")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="hann",
                    help="FFT window (default: hann)")
    ap.add_argument("--pad", type=int, default=8,
                    help="zero-padding factor for peak refinement (default: 8)")
    ap.add_argument("--detrend", choices=["mean", "linear", "none"], default="mean",
                    help="trend removed before windowing (default: mean)")
    ap.add_argument("--fmin", type=float, default=None,
                    help="ignore peaks below this frequency "
                         "(default: 1.5 / record length, which skips DC)")
    ap.add_argument("--fmax", type=float, default=None, help="ignore peaks above this frequency")
    ap.add_argument("--top", type=int, default=3, help="how many peaks to list (default: 3)")
    ap.add_argument("--ini", help="singleideal*.ini holding the reference state "
                                  "(default: auto-detected next to the progress file)")
    ap.add_argument("--no-ini", action="store_true",
                    help="do not read any .ini; report the raw frequency only")
    ap.add_argument("--U", type=float, default=None,
                    help="reference velocity, overriding uref from the .ini")
    ap.add_argument("--D", type=float, default=1.0,
                    help="cylinder diameter in metres (default: 1)")
    ap.add_argument("--no-force-stats", action="store_true",
                    help="skip the mean/peak force table")
    ap.add_argument("--csv", help="write the spectrum to this CSV file")
    ap.add_argument("--plot", action="store_true", help="show signal + spectrum")
    pio.add_column_args(ap)
    pio.add_force_scale_args(ap)
    args = ap.parse_args(argv)

    results = []
    for path in args.files:
        pr = pio.Progress(path, pio.cols_from_args(args))
        ini_path = pio.find_ini(path, args.ini)
        ini = pio.parse_ini(ini_path) if (ini_path and not args.no_ini) else None
        pr.force_scale, scale_note = pio.force_scale_from_args(args, ini, args.D)
        t = pr.time
        axis = {"lift": "y", "drag": "x", "fx": "x", "fy": "y", "fz": "z"}[args.signal]
        y = pr.force(axis, args.kind)

        m = np.ones(len(t), dtype=bool)
        if args.t_start is not None:
            m &= t >= args.t_start
        if args.t_end is not None:
            m &= t <= args.t_end
        t, y = t[m], y[m]
        if len(t) < 16:
            raise SystemExit(f"{path}: fewer than 16 samples left after time filtering")

        span = t[-1] - t[0]
        freq, amp, dt, n, nfft = spectrum(t, y, args.window, args.pad, args.detrend)

        fmin = args.fmin if args.fmin is not None else 1.5 / span
        fmax = args.fmax if args.fmax is not None else freq[-1]
        band = (freq >= fmin) & (freq <= fmax)
        if not band.any():
            raise SystemExit(f"{path}: no spectral bins inside [{fmin}, {fmax}]")

        idx = np.flatnonzero(band)
        k = int(idx[np.argmax(amp[idx])])
        delta, a_peak = parabolic_peak(amp, k)
        df = freq[1] - freq[0]
        f_peak = (k + delta) * df

        f_zc = zero_crossing_frequency(t, y)

        print(f"\n{path}   [{args.signal}, {args.kind}]")
        print(f"  force normalisation: {scale_note}")
        print(f"  samples {n}  span {span:.6g}  dt {dt:.6g}  "
              f"f_Nyquist {0.5/dt:.6g}")
        print(f"  bin spacing {1.0/(n*dt):.4g} (unpadded) -> {df:.4g} "
              f"with {args.pad}x zero-pad   (nfft={nfft})")
        print(f"  PEAK FREQUENCY   f = {f_peak:.8g}")
        print(f"  period           T = {1.0/f_peak:.8g}")
        print(f"  amplitude        A = {a_peak:.6g}  "
              f"(time-domain sqrt(2)*RMS' = {np.std(y)*np.sqrt(2):.6g})")
        if f_zc:
            print(f"  zero-crossing cross-check: f = {f_zc:.6g}  "
                  f"({100*abs(f_zc-f_peak)/f_peak:.2f}% from FFT)")
        print(f"  cycles resolved  {span*f_peak:.1f}")

        ref = None
        if not args.no_ini:
            if ini is not None:
                ref = pio.reference_state(ini, args.D)
                if args.U:
                    ref["U"] = args.U
                    ref["Re"] = ref["rho"] * args.U * args.D / ref["mu"]
                    ref["Mach"] = args.U / ref["a"]
            elif args.U:
                ref = dict(U=args.U, D=args.D, Re=None, Mach=None, path=None)
            else:
                print("  (no singleideal*.ini found; pass --ini or --U for Strouhal)")

        if ref:
            U, D = ref["U"], args.D
            St = f_peak * D / U
            if ref.get("Re") is not None:
                print(f"  reference state from {os.path.basename(ini_path)}:")
                print(f"    T = {ref['T']:g} K   p = {ref['p']:g} Pa   "
                      f"U = {U:g} m/s   D = {D:g} m")
                print(f"    rho = {ref['rho']:.6f} kg/m3   "
                      f"mu = {ref['mu']:.6f} Pa s   a = {ref['a']:.2f} m/s")
                print(f"    REYNOLDS  Re = rho*U*D/mu = {ref['Re']:.1f}   "
                      f"MACH  M = {ref['Mach']:.4f}")
            print(f"  STROUHAL  St = f*D/U = {St:.6f}   (U = {U:g} m/s, D = {D:g} m)")
            if ref.get("Re") is not None and 49.0 < ref["Re"] < 180.0:
                # Williamson & Brown (1998) laminar-shedding correlation.
                st_ref = 0.2731 - 1.1129 / np.sqrt(ref["Re"]) + 0.4821 / ref["Re"]
                print(f"    Williamson-Brown correlation at Re = {ref['Re']:.1f}: "
                      f"St = {st_ref:.6f}  ({100*(St-st_ref)/st_ref:+.2f}%)")

        # Harmonic content, which tells lift (odd) from drag (even) signals.
        for h in (2, 3):
            j = int(round(h * f_peak / df))
            if j < len(amp):
                print(f"  harmonic {h}f = {h*f_peak:.6g}: "
                      f"A = {amp[j]:.4g}  ({100*amp[j]/a_peak:.2f}% of peak)")

        if not args.no_force_stats:
            # Always report both components, whichever one was spectrally
            # analysed.  Bin on the lift period: for the lift that is one
            # oscillation, for the drag two (it runs at 2*f_s).
            fy = pr.force("y", args.kind)[m]
            fx = pr.force("x", args.kind)[m]
            t_lift = 1.0 / f_peak if axis == "y" else 2.0 / f_peak
            sx = pio.force_stats(t, fx, t_lift)
            sy = pio.force_stats(t, fy, t_lift)
            ncyc = sy.get("n_cycles", 0)
            print(f"  FORCE STATISTICS  (reference area as above, "
                  f"{ncyc} whole shedding cycles)")
            print(pio.format_force_stats(sx, sy))

        if args.top > 1:
            # Local maxima in the band, strongest first.
            loc = idx[1:-1]
            loc = loc[(amp[loc] > amp[loc - 1]) & (amp[loc] > amp[loc + 1])]
            order = loc[np.argsort(amp[loc])[::-1][: args.top]]
            print(f"  strongest {len(order)} peaks:")
            for j in order:
                d, a = parabolic_peak(amp, int(j))
                print(f"    f = {(j+d)*df:12.8g}   A = {a:.6g}")

        if args.csv:
            out = args.csv if len(args.files) == 1 else \
                f"{os.path.splitext(args.csv)[0]}_{os.path.basename(path)}.csv"
            sel = freq <= (fmax if args.fmax else 20 * f_peak)
            np.savetxt(out, np.column_stack([freq[sel], amp[sel]]),
                       delimiter=",", header="frequency,amplitude", comments="")
            print(f"  -> wrote {out}")

        results.append((path, t, y, freq, amp, f_peak, a_peak, span))

    if args.plot:
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(2, 1, figsize=(10, 6))
        for path, t, y, freq, amp, f_peak, a_peak, span in results:
            lab = os.path.basename(path)
            ax[0].plot(t, y, lw=0.7, label=lab)
            sel = freq <= 8 * f_peak
            ax[1].semilogy(freq[sel], np.maximum(amp[sel], 1e-16), lw=0.9, label=lab)
            ax[1].plot([f_peak], [a_peak], "v", ms=6)
            ax[1].annotate(f"f = {f_peak:.4g}", (f_peak, a_peak),
                           textcoords="offset points", xytext=(6, 4), fontsize=8)
        ax[0].set_xlabel("time")
        ax[0].set_ylabel(f"{args.signal} ({args.kind})")
        ax[1].set_xlabel("frequency")
        ax[1].set_ylabel("amplitude")
        ax[1].grid(True, which="both", alpha=0.3)
        for a in ax:
            a.legend(fontsize=8)
        fig.tight_layout()
        plt.show()

    return 0


if __name__ == "__main__":
    sys.exit(main())
