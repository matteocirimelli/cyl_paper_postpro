# Validating the cylinder runs against the literature

How to compare `progress_Re*.out` against the two reference papers in this
folder, which numbers are legitimately comparable, and which are not.

Read `USAGE.md` first for the scripts, `README.md` for the column layout.

---

## 0. Which copy of the Williamson paper to use

There are two conversions of the same PDF. **Use `ref_paper_cyl_v2/`.**

| | `ref_paper_cyl/` (v1) | `ref_paper_cyl_v2/` (v2) |
|---|---|---|
| images | JPEG, lossy | PNG, lossless |
| Figure 21 crop | **41 x 559 px — broken sliver, unusable** | 820 x 1200 px, both panels, legible |
| Figure 7 crop | 1017 x 1071 px | 964 x 968 px, formula readable |
| front matter | none | slug/title/pages/outline/`qa_flags` |
| companion files | — | `.refs.md` with the numbered bibliography |

v2's Figure 21 is the single most important plot for this work and v1 lost it
entirely. v2 also ships `.refs.md`, which is what lets you resolve
"Ref.[10]" style citations into actual papers.

**Neither conversion recovers the one formula you need** (Section 2) — it is
drawn inside a figure, so no text converter will ever extract it. In v2 you can
at least *read* it off `_page_17_Figure_2.png` directly, without rendering the
PDF. That is the practical difference.

Two more things no converter will fix, because they are errors in the printed
original:

* p. 517 quotes Henderson's 2-D fit as
  `S = 0.2417 - 0.8328 Re exp(-0.001895 Re)`. This is a **typo in the journal**
  — it evaluates to about -68 at Re = 100. Do not use it.
* The Annual Reviews PDF text layer is OCR of a scan, so numbers inside
  figures are unreliable everywhere. Read plots from the v2 PNGs, or render
  the PDF at 600 dpi.

---

## 1. What to run

```bash
# 1. cut the transient, keep whole cycles
python3 filter_transient.py progress_Re100.out progress_Re150.out --whole-cycles

# 2. report, in the normalisation the papers use (see Section 3)
python3 fft_peak.py progress_Re100_stat.out --U 38 --D 1 --span 0.8944272
python3 fft_peak.py progress_Re150_stat.out --U 57 --D 1 --span 0.8944272
```

Then read off the `STROUHAL` line and the `FORCE STATISTICS` table.

---

## 2. Reference values: Strouhal number

### Williamson (1996), *Annu. Rev. Fluid Mech.* **28**:477-539

**The authoritative number is a formula, not a plot read-off.** It is printed
*inside* the right-hand inset panel of **Figure 7a, page 494**
(`ref_paper_cyl_v2/_page_17_Figure_2.png`, top-right panel):

```
S0 = -3.3265/Re + 0.1816 + 1.6e-4 * Re          (laminar parallel shedding, Re ~ 49-180)
```

| Re | S0 |
|---|---|
| 100 | 0.1643 |
| 150 | 0.1834 |
| 101.7 | 0.1652 |
| 152.5 | 0.1842 |

Corroborating plots, if you want a scatter band rather than a single curve:

| where | page | v2 image | value at Re=100 | at Re=150 |
|---|---|---|---|---|
| Fig. 11a, "parallel laminar shedding" branch | 502 | `_page_25_Figure_2.png` | 0.164 | 0.184 |
| Fig. 21a, experiment (Williamson 1992, open circles) | 517 | `_page_44_Figure_2.png` upper | 0.164 | 0.184 |
| Fig. 21a, 2-D DNS (Karniadakis & Kedar; Thompson) | 517 | same | 0.165-0.168 | ~0.186 |

Ignore the upper dashed curve in Fig. 21a (Zhang et al. 1995, 2-D: 0.172 and
0.191) — the text itself calls it "surprisingly high compared to all the other
simulations".

### Ming Pingjian & Zhang Wenping (2009), *Chinese J. Aeronautics* **22**:480-485

**Table 1, page 483**, `Re = 100` only. In the markdown the table is at
`ref_paper_cyl_chinese.md:219` but **its columns are mangled** — the converter
split `1.35±0.014` across two cells and dropped the header. The true table
(verified against the source PDF, `/home/matteo/pdf2md/test_pdfs/`) is:

| source | C_D | C_L | Sr |
|---|---|---|---|
| Ref.[10] Kim, Kim & Choi 2001, *JCP* 171:132 | 1.33 | ±0.320 | 0.165 |
| Ref.[11] Calhoun 2002, *JCP* 176:231 | 1.35 ± 0.014 | ±0.300 | 0.175 |
| Ref.[12] Russell & Wang 2003, *JCP* 191:177 | 1.38 ± 0.007 | ±0.322 | 0.169 |
| Ref.[13] Choi et al. 2007, *JCP* 224:757 | 1.34 ± 0.011 | ±0.315 | 0.164 |
| Proposed (this paper) | 1.34 ± 0.008 | ±0.313 | 0.165 |

The `±` on C_D is the **drag fluctuation amplitude**, not an uncertainty. The
`±` on C_L is the **lift amplitude**. Both are half peak-to-peak, which is what
`cycle amplitude` reports.

Their definitions, Eqs. (28)-(29) p. 483: `C = F / (rho * u_in^2 * d / 2)` —
i.e. per unit span, referred to the diameter. This matters; see Section 3.

Also usable: **Fig. 4, p. 483** (`_page_3_Figure_22.png`) plots C_D and C_L vs
time with readable axes (C_D from 1.330 to 1.350, C_L from -0.35 to 0.35), so
you can eyeball waveform shape against yours.

---

## 3. Normalisation: get this right before comparing anything

Two corrections stand between the solver output and a comparable coefficient.

**(a) Reference area.** The solver writes `F / (qref * arearef)` with
`arearef = 0.785` from the `.ini`. The papers use `D * span`. The solver sums
over the whole spanwise extent, which from `out.log` is `0.8944272`, so:

```
--span 0.8944272        # factor 0.877657 -- USE THIS for paper comparisons
```

The default (`--span 1`) leaves a stray span factor of 0.894 in the numbers and
is **not** comparable to the literature. Multiply default output by 1.11804 to
convert.

**(b) The forces are pressure-only.** This file format carries no viscous
force (see `README.md`). Consequences:

| quantity | comparable? | why |
|---|---|---|
| Strouhal number | **yes** | normalisation-independent |
| C_L amplitude | **yes** | friction lift is a few percent at these Re |
| C_D fluctuation | **yes, roughly** | dominated by pressure |
| **mean C_D** | **NO** | missing the friction contribution entirely |

Do not report mean C_D against Table 1 without saying it is pressure drag only.

---

## 4. Current results and how they compare

Averaged over the **last 4 shedding cycles** (see Section 5 on why not the
whole window), with `--span 0.8944272`:

| | Re = 101.7 | Re = 152.5 |
|---|---|---|
| St | 0.1702 | 0.1885 |
| mean C_D (pressure only) | 1.0344 | 1.0817 |
| C_D fluctuation (±) | 0.0096 | 0.0202 |
| C_L amplitude (±) | 0.3018 | 0.4893 |

### Strouhal

| | yours | Williamson S0 | dev |
|---|---|---|---|
| Re = 101.7 | 0.1702 | 0.1652 | **+3.0%** |
| Re = 152.5 | 0.1885 | 0.1842 | **+2.3%** |

Against the Chinese Table 1 spread at Re = 100 (0.164-0.175), your 0.1702 sits
comfortably inside — between Kim/Choi (0.164-0.165) and Calhoun (0.175).

Note `fft_peak.py` prints a *different* reference, the Williamson & Brown
(1998) four-term correlation (0.1675 / 0.1862, giving +1.6% / +1.3%). Decide
which you cite and be consistent; they differ by ~0.002.

### Forces, Re = 100

| | yours | Table 1 range | verdict |
|---|---|---|---|
| C_L amplitude | ±0.302 | ±0.300 to ±0.322 | **inside the band** |
| C_D fluctuation | ±0.0096 | ±0.007 to ±0.014 | **mid-band** |
| mean C_D | 1.034 (pressure) | 1.33-1.38 (total) | gap 0.30-0.35 |

The C_D gap is the missing friction drag, and 0.30 is the right order of
magnitude for a cylinder at Re = 100. **But neither paper decomposes the drag,
so this is an inference, not a verified match.** To close it properly you need
either the viscous force columns from the solver (`--col-fv-x`, if a future
build writes them) or a third reference that reports the pressure/friction
split — Park, Kwon & Choi (1998) is the usual one.

### Forces, Re = 150

**Neither paper gives force coefficients at Re = 150.** Williamson has no C_D
data at all — p. 487 states outright that "the asymptotic formula for C_D(Re)
for this steady wake is not yet available" — and the Chinese paper only runs
Re = 100. For Re = 150 you have the Strouhal comparison and nothing else.
Finding a third reference is the open task.

### Base suction, if you ever extract rear-surface pressure

Not obtainable from `progress_*.out` (it has no surface pressure), but for
reference, from **Fig. 21b, p. 517** (`_page_44_Figure_2.png`, lower panel),
digitised twice independently:

| Re | experiment (Williamson & Roshko 1990) | 2-D DNS (Henderson 1995) |
|---|---|---|
| 100 | -C_pb ~ 0.70 | ~0.73 |
| 150 | -C_pb ~ 0.85 | ~0.88 |

Cross-checked against Fig. 3, p. 483 (`_page_6_Figure_4.png`). Read-off
accuracy about ±0.02.

---

## 5. Convergence caveats

`fft_peak.py` now prints `peak scatter` and `mean drift`. Both flagged the
records as still moving:

* **Re = 100** — mean drag fell monotonically across the 8 retained cycles
  (0.936 -> 0.925 in `--span 1` units, -1.2%). The per-cycle steps shrink to
  ~1e-4 by the end, so it is nearly settled, but the mean over the *whole*
  window (1.0372) is contaminated by the early cycles. The last-4-cycle mean
  (1.0344) is the better number.
* **Re = 150** — rose over the first ~4 cycles and is flat thereafter
  (last-cycle-to-last-cycle change 1e-4). Fine.

The `filter_transient.py` default `--tol 0.05` is too loose for the mean drag,
because the drag *fluctuation* is tiny (C_D' ~ 0.008) so a slow drift in the
mean does not trip a 5% test on the lift amplitude. Either:

```bash
python3 filter_transient.py progress_Re100.out --whole-cycles --tol 0.01
```

or just run the case longer. The lift is converged either way (peak scatter
3.3%), so **St and C_L are trustworthy now; mean C_D is the one to re-check.**

---

## 6. Open items

1. Viscous force columns are absent — mean C_D cannot be validated as it
   stands. Confirm whether the solver build can be made to write them.
2. No force reference at Re = 150 in either paper. Needs a third source.
3. Both your runs are compressible (M = 0.109 and 0.164) while every reference
   here is incompressible. At M = 0.164 the compressibility correction to C_D
   is roughly 1-2% — small, but worth stating in a write-up rather than
   ignoring.
4. Williamson's laminar range is quoted for *parallel* shedding up to
   Re_crit = 194 (pp. 503-504; Barkley & Henderson's Floquet analysis gives
   188.5 ± 1.0). Both your cases are below that, so a 2-D simulation is
   physically justified — worth citing explicitly.
