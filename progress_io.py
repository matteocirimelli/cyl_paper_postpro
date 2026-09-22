"""Shared reader for the CFD solver's progress_*.out files.

Column layout, confirmed against the solver source
(streams_3/code_unstructured/src/singleideal/singleideal_gpu.F90,
subroutine print_progress, laminar branch):

     1  iteration
     2  dt
     3  time
     4  residual rho*u  (normalised by wref)
     5  residual rho*e  (normalised by wref)
   6-8  pressure force  x, y, z
  9-10  rho  min, max
 11-12  T    min, max
 13-14  p    min, max

That accounts for 14 of the 17 columns these files actually contain; the
trailing three are written by a locally modified build that does not match any
source variant found on this machine.  Column 15 is NOT the viscous drag: it
oscillates at the shedding frequency f_s with zero mean, whereas viscous drag
must oscillate at 2*f_s about a positive mean.  Columns 16-17 are O(1e-17),
i.e. numerically zero.  So the viscous forces are not available in this file,
and `fv_*` has no default -- ask for them only with an explicit --col-fv-*.

IMPORTANT -- the forces are already non-dimensional.  The solver writes
    F / (qref * arearef),   qref = 0.5 * rhoref * uref**2
with `arearef` taken from the .ini.  To convert to a coefficient referred to a
different area (e.g. the true frontal area D * span), multiply by
arearef / A_wanted.  See `force_rescale`.
"""

import numpy as np

# 1-based defaults, matching the header above.  The viscous-force entries are
# deliberately None: this file format does not carry them.
DEFAULT_COLS = {
    "iter": 1,
    "dt": 2,
    "time": 3,
    "res_rhou": 4,
    "res_rhoe": 5,
    "fp_x": 6,
    "fp_y": 7,
    "fp_z": 8,
    "rho_min": 9,
    "rho_max": 10,
    "t_min": 11,
    "t_max": 12,
    "p_min": 13,
    "p_max": 14,
    "fv_x": None,
    "fv_y": None,
    "fv_z": None,
}


class Progress:
    """A parsed progress file: numeric table plus the original text lines."""

    def __init__(self, path, cols=None):
        self.path = path
        self.cols = dict(DEFAULT_COLS)
        if cols:
            self.cols.update({k: v for k, v in cols.items() if v is not None})

        # Multiplies every force returned by force(); see set_force_scale().
        self.force_scale = 1.0

        raw, rows = [], []
        with open(path) as fh:
            for line in fh:
                if not line.strip() or line.lstrip().startswith(("#", "!", "%")):
                    continue
                try:
                    rows.append([float(tok) for tok in line.split()])
                except ValueError:
                    continue  # header / text line
                raw.append(line.rstrip("\n"))

        if not rows:
            raise ValueError(f"{path}: no numeric rows found")

        ncol = min(len(r) for r in rows)
        self.data = np.array([r[:ncol] for r in rows], dtype=float)
        self.raw = raw
        self.ncol = ncol

        needed = max(v for v in self.cols.values() if v is not None)
        if ncol < needed:
            raise ValueError(
                f"{path}: file has {ncol} columns but column {needed} was requested"
            )

    def col(self, name):
        """Column by logical name, as a 1-D array."""
        return self.data[:, self.cols[name] - 1]

    # -- convenience accessors -------------------------------------------
    @property
    def time(self):
        return self.col("time")

    @property
    def iteration(self):
        return self.col("iter")

    def force(self, axis, kind="pressure"):
        """Force component. axis in 'xyz', kind in {'pressure','viscous','total'}.

        Values are already divided by qref*arearef, i.e. they are coefficients.
        'viscous' and 'total' need --col-fv-* because this file format does not
        write the viscous forces.
        """
        p = self.col(f"fp_{axis}") * self.force_scale
        if kind == "pressure":
            return p
        if self.cols.get(f"fv_{axis}") is None:
            raise SystemExit(
                f"kind='{kind}' needs the viscous force, which this file does not\n"
                f"contain (see progress_io.py). Use --kind pressure, or point at\n"
                f"the right column explicitly with --col-fv-{axis}."
            )
        v = self.col(f"fv_{axis}") * self.force_scale
        return v if kind == "viscous" else p + v


def add_column_args(parser):
    """Register the --col-* overrides on an argparse parser."""
    g = parser.add_argument_group("column overrides (1-based)")
    for name, default in DEFAULT_COLS.items():
        g.add_argument(
            f"--col-{name.replace('_', '-')}",
            dest=f"col_{name}",
            type=int,
            default=None,
            metavar="N",
            help=f"column holding {name} "
                 f"(default: {default if default is not None else 'unset'})",
        )
    return parser


def cols_from_args(args):
    return {name: getattr(args, f"col_{name}", None) for name in DEFAULT_COLS}


def uniform_resample(t, y):
    """Interpolate (t, y) onto a uniform time grid.

    The solver's dt drifts by ~2% over a run, so the samples are not exactly
    equispaced in time even though they are equispaced in iteration.  The FFT
    assumes a uniform grid, so resample onto one with the median spacing.
    """
    dt = np.median(np.diff(t))
    n = int(np.floor((t[-1] - t[0]) / dt)) + 1
    tu = t[0] + dt * np.arange(n)
    return tu, np.interp(tu, t, y), dt


def cycle_extrema(t, y, period):
    """Per-cycle maxima and minima, over whole cycles of length `period`.

    The record is cut into consecutive bins of one `period` starting at t[0];
    the trailing partial cycle is dropped.  Averaging the per-cycle extrema is
    more robust than taking a single global max/min, and their scatter is a
    direct measure of how well converged the shedding is.
    """
    ncyc = int((t[-1] - t[0]) // period)
    mx, mn = [], []
    for i in range(ncyc):
        m = (t >= t[0] + i * period) & (t < t[0] + (i + 1) * period)
        if m.sum() < 3:
            continue
        mx.append(y[m].max())
        mn.append(y[m].min())
    return np.array(mx), np.array(mn)


def force_stats(t, y, period=None):
    """Mean / fluctuation / peak statistics of one force-coefficient signal.

    Keys always present:
        mean, rms (about the mean, i.e. C'), amp_rms (= sqrt(2)*rms),
        min, max, ptp, amp_ptp (= ptp/2)
    Added when `period` is given:
        n_cycles, peak_max, peak_min and their standard deviations,
        amp_cycle (= half the mean peak-to-peak per cycle)

    Note the drag oscillates at 2*f_s while the lift oscillates at f_s, so one
    lift period contains two drag cycles.  Binning both on the lift period
    still captures the correct extrema, only with two drag peaks per bin.
    """
    y = np.asarray(y, dtype=float)
    out = {
        "mean": float(y.mean()),
        "rms": float(np.std(y)),
        "amp_rms": float(np.std(y) * np.sqrt(2.0)),
        "min": float(y.min()),
        "max": float(y.max()),
        "ptp": float(y.max() - y.min()),
        "amp_ptp": float(0.5 * (y.max() - y.min())),
    }
    if period and period > 0.0:
        mx, mn = cycle_extrema(t, y, period)
        if mx.size:
            out.update(
                n_cycles=int(mx.size),
                peak_max=float(mx.mean()), peak_max_std=float(mx.std()),
                peak_min=float(mn.mean()), peak_min_std=float(mn.std()),
                amp_cycle=float(0.5 * (mx.mean() - mn.mean())),
            )
        # Drift of the cycle mean across the record.  A converged run has this
        # at the noise level; anything larger means the mean is still moving
        # and the reported mean is not yet a converged value.
        cm = []
        for i in range(int((t[-1] - t[0]) // period)):
            m = (t >= t[0] + i * period) & (t < t[0] + (i + 1) * period)
            if m.sum() >= 3:
                cm.append(y[m].mean())
        if len(cm) >= 2:
            out["mean_first_cycle"] = float(cm[0])
            out["mean_last_cycle"] = float(cm[-1])
            out["drift"] = float(cm[-1] - cm[0])
    return out


def format_force_stats(stats_x, stats_y, indent="    "):
    """Two-column text table of drag (x) and lift (y) statistics."""
    rows = [
        ("mean", "mean", "C_D = {:+.6f}"),
        ("rms about mean  (C')", "rms", "{:+.6f}"),
        ("amplitude  sqrt(2)*rms", "amp_rms", "{:+.6f}"),
        ("cycle peak  max", "peak_max", "{:+.6f}"),
        ("cycle peak  min", "peak_min", "{:+.6f}"),
        ("cycle amplitude", "amp_cycle", "{:+.6f}"),
        ("global min", "min", "{:+.6f}"),
        ("global max", "max", "{:+.6f}"),
        ("peak-to-peak", "ptp", "{:+.6f}"),
    ]
    lines = [f"{indent}{'':<26}{'drag  C_x':>14}{'lift  C_y':>16}"]
    for label, key, _ in rows:
        if key not in stats_x and key not in stats_y:
            continue
        cells = []
        for s in (stats_x, stats_y):
            cells.append(f"{s[key]:+.6f}" if key in s else "--")
        lines.append(f"{indent}{label:<26}{cells[0]:>14}{cells[1]:>16}")
    # Convergence indicators: scatter of the per-cycle peaks, and how far the
    # cycle mean moved from the first retained cycle to the last.
    if "peak_max_std" in stats_x and "peak_max_std" in stats_y:
        sx = 100.0 * stats_x["peak_max_std"] / max(abs(stats_x["amp_cycle"]), 1e-30)
        sy = 100.0 * stats_y["peak_max_std"] / max(abs(stats_y["amp_cycle"]), 1e-30)
        lines.append(f"{indent}{'peak scatter (% of ampl.)':<26}"
                     f"{sx:>13.2f}%{sy:>15.2f}%")
    if "drift" in stats_x and "drift" in stats_y:
        lines.append(f"{indent}{'mean drift, 1st->last cycle':<26}"
                     f"{stats_x['drift']:>+14.6f}{stats_y['drift']:>+16.6f}")
    return "\n".join(lines)


def estimate_period(t, y, nlast=0.5):
    """Crude shedding period from mean-upcrossings of the last `nlast` of the record.

    Returns None if fewer than two upcrossings are found.
    """
    i0 = int(len(t) * (1.0 - nlast))
    ts, ys = t[i0:], y[i0:]
    d = ys - ys.mean()
    up = np.flatnonzero((d[:-1] < 0) & (d[1:] >= 0))
    if up.size < 2:
        return None
    # Linear interpolation of each crossing instant.
    frac = -d[up] / (d[up + 1] - d[up])
    tc = ts[up] + frac * (ts[up + 1] - ts[up])
    return float(np.mean(np.diff(tc)))


# ---------------------------------------------------------------------------
# Reference state, read from the solver's singleideal*.ini
# ---------------------------------------------------------------------------

import glob
import math
import os
import re

# Solver defaults, used when a key is absent from the .ini.
INI_DEFAULTS = {
    "rgas": 287.303891077476,
    "gamma": 1.4,
    "sutherland": (0.00001765, 273.0, 111.0),
    "prandtl": 0.72,
}


def parse_ini(path):
    """Read the reference state from a singleideal*.ini.

    Only the handful of keys needed for Re / M / St are extracted; the rest of
    the file is ignored.  Comments start with '#' or ';'.
    """
    vals = {}
    with open(path) as fh:
        for line in fh:
            line = line.split("#", 1)[0].split(";", 1)[0].strip()
            if "=" not in line:
                continue
            key, _, rhs = line.partition("=")
            key = key.strip().lower()
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eEdD][-+]?\d+)?", rhs)
            if not nums:
                continue
            nums = [float(n.replace("d", "e").replace("D", "e")) for n in nums]
            vals[key] = nums[0] if len(nums) == 1 else tuple(nums)

    ref = dict(INI_DEFAULTS)
    for k in ("tref", "pref", "uref", "rgas", "gamma", "sutherland", "prandtl",
              "lref", "arearef", "aoa"):
        if k in vals:
            ref[k] = vals[k]
    missing = [k for k in ("tref", "pref", "uref") if k not in ref]
    if missing:
        raise ValueError(f"{path}: missing reference quantities {missing}")
    ref["path"] = path
    return ref


def sutherland_viscosity(T, coeffs):
    """mu(T) = mu0 * (T/T0)^1.5 * (T0+S)/(T+S).

    This is the convention the solver uses: with sutherland = 0.4085, 273.15,
    110.4 and T = 300 K it reproduces the 'Mixture viscosity' printed in
    out.log (0.4394270329919647) to machine precision.
    """
    mu0, T0, S = coeffs
    return mu0 * (T / T0) ** 1.5 * (T0 + S) / (T + S)


def reference_state(ini, D=1.0):
    """Derive rho, mu, sound speed, Reynolds and Mach from a parsed .ini."""
    T, p, U = ini["tref"], ini["pref"], ini["uref"]
    R, g = ini["rgas"], ini["gamma"]
    rho = p / (R * T)
    mu = sutherland_viscosity(T, ini["sutherland"])
    a = math.sqrt(g * R * T)
    return dict(
        T=T, p=p, U=U, R=R, gamma=g, D=D,
        rho=rho, mu=mu, nu=mu / rho, a=a,
        Re=rho * U * D / mu, Mach=U / a,
    )


def find_ini(progress_path, explicit=None):
    """Locate the .ini belonging to a progress file.

    Matches on the case tag in the filename (progress_Re100_stat.out ->
    singleideal_Re100.ini); falls back to a unique singleideal*.ini in the
    same directory.  Returns None if nothing can be matched unambiguously.
    """
    if explicit:
        return explicit
    d = os.path.dirname(os.path.abspath(progress_path)) or "."
    stem = os.path.splitext(os.path.basename(progress_path))[0]
    tag = re.sub(r"^progress", "", stem)
    tag = re.sub(r"_stat$", "", tag).strip("_")

    if tag:
        hits = sorted(glob.glob(os.path.join(d, f"singleideal*{tag}*.ini")))
        if len(hits) == 1:
            return hits[0]
    hits = sorted(glob.glob(os.path.join(d, "singleideal*.ini")))
    return hits[0] if len(hits) == 1 else None


def force_rescale(arearef, area_wanted):
    """Factor converting the solver's coefficients to a different reference area.

    The solver writes F / (qref * arearef).  Multiplying by this factor refers
    the coefficient to `area_wanted` instead:

        C = F / (qref * area_wanted) = written_value * arearef / area_wanted
    """
    if area_wanted <= 0.0:
        raise ValueError("reference area must be positive")
    return arearef / area_wanted


def add_force_scale_args(parser):
    """Register the reference-area correction flags."""
    g = parser.add_argument_group("force normalisation")
    g.add_argument("--arearef", type=float, default=None,
                   help="arearef the solver divided by (default: read from the .ini)")
    g.add_argument("--ref-area", type=float, default=None, metavar="A",
                   help="reference area the coefficients should use "
                        "(default: D * --span)")
    g.add_argument("--span", type=float, default=1.0,
                   help="spanwise length in the reference area (default: 1, i.e. "
                        "the reference area is just the diameter D)")
    g.add_argument("--raw-forces", action="store_true",
                   help="leave the forces exactly as the solver wrote them")
    return parser


def force_scale_from_args(args, ini=None, D=1.0):
    """Work out the factor that re-refers the solver's coefficients.

    Returns (factor, description).  The solver divides by qref*arearef with
    arearef taken from the .ini; this undoes that and divides by D*span
    instead.
    """
    if getattr(args, "raw_forces", False):
        return 1.0, "as written by the solver (arearef from the .ini)"

    arearef = args.arearef
    if arearef is None and ini is not None:
        arearef = ini.get("arearef")
    if arearef is None:
        return 1.0, "unscaled (no arearef found; pass --arearef to correct)"

    wanted = args.ref_area
    if wanted is None:
        wanted = D * args.span
    factor = force_rescale(arearef, wanted)
    return factor, (f"arearef {arearef:g} -> reference area {wanted:g} "
                    f"(x {factor:.6f})")
