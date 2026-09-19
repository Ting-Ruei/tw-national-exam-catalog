---
name: extract-exam-paper-structure
description: Recover the structure of a Taiwan national-exam paper from its official PDF — question segments, the text skeleton, figure and per-option image crops — using rules that hold on two engines and are stated in terms of the paper's own geometry. Use when rebuilding a question bank from official PDFs, segmenting a paper into questions, deciding where a figure belongs, cutting a figure or answer-option image out of a page, or diagnosing a crop that is empty, truncated, or cut off at the bottom.
---

# Extract Exam Paper Structure

Turn one official PDF into the smallest set of claims a human can check: which text is which
question, and which picture belongs to which question or option.

Every number in this skill is measured. Rules that were only reasoned about are marked as such and
must be measured before use.

## Non-negotiable boundaries

- **The paper is the authority on structure.** The question bank's own grouping is not: 1052 微生物
  Q7's four test tubes are four images in the bank and ONE figure on the paper. Cut what the paper
  printed.
- **The answer sheet is the authority on question count.** If a paper yields 79 questions and the
  sheet lists 80, the paper is wrong, not the sheet.
- **A rule must hold on BOTH engines.** A rule that only works in one extractor is a description of
  that extractor, not of the paper. Measure the disagreement; do not average it away.
- **A rule must be stated in terms of the paper.** "The gap is more than 12 pt" is an engine
  internal. "The question owns from its own number to the next question's number" is the paper.
- **Cutting a crop is measurement; describing it is opinion.** Never let a model's description
  decide whether a crop is written.
- **AI is advisory (GOV-05).** Never auto-accept, auto-block, or write a human review event.
- **A crop that is written but not shown is not evidence.** Whatever is cut must be reachable in the
  review UI, or the claim cannot be checked.

## The pipeline, and the one order that matters

```
official PDF ──▶ structure (reflow) ──▶ text repair ──▶ package ──▶ crop ──▶ merge ──▶ review UI
```

**`package → crop → merge`.** Crops are cut against the packaged run and then adopted into the
merged queue. Running merge before crop produces a queue whose `image_refs` point at files this run
never wrote.

## 1. Segmenting a paper into questions

1. Build the text skeleton from the PDF's own line geometry. Two engines must agree on the count.
2. Segment by **successor**: question *n* owns from its own number to the next question's number
   **on the same page**. Do not use a distance threshold.
3. A number-like token is a question number only if it is a successor (`seen != last + 1` breaks
   the run). The leftmost-cell and margin checks (`_MARGIN_TOLERANCE`) decide whether a number
   starts a question or is content.
4. **A wrapped line is not a question.** A successor number whose line is the tail of the previous
   sentence is content. Five conditions, all required: a question is being read; it has shown no
   option mark yet; **the next line carries ≥2 option marks**; the line before it does not end a
   sentence (`？?。！!：:；;`); and the line's own text is ≤14 characters.

   Measured: **89,463 successor anchors corpus-wide, exactly 2 satisfy all five** — one is the real
   defect (`1001_藥師_藥劑學` Q1, stem ends `…經過` and wraps as `2 天後，藥物濃度為何？`, so Q2 stole
   Q1's options), the other is a table artifact. Cost: 0 real questions. Do **not** implement this
   as an indent/distance rule: 26 of 89,463 real anchors break any x-threshold, while `2 天後`
   shares the continuation indent.
5. Reconcile against the answer sheet. **Count mismatch is an error**, never a warning.

Measured: **3,516/3,516 papers agree between engines on the count (100.0%)** over the full corpus.

### 1b. One paper can use two private-use alphabets

A paper may print **two** PUA families doing different jobs. `1001_醫事檢驗師_臨床生理學與病理學` Q65:

```
泌尿系統感染的尿液分析以下列那些物質的增加為主？
\ue000細菌  \ue001白血球  \ue002紅血球  \ue003葡萄糖     ← sub-items the question asks about
\ue18c僅\ue000\ue001\ue002  \ue18d僅\ue002\ue003  …        ← the four options the candidate chooses between
```

- `option_alphabet_families()` returns the families, **most-used first**; the option labels are
  printed once per question, so the larger total is the alphabet and the other is content.
- At segmentation, **try each family and keep the one that yields exactly the declared option
  count**. Refusal is still the default.
- The alphabet test is **length == option count**, not "all counts equal": a family of four
  consecutive codepoints *is* the alphabet even when one member is used once fewer (measured:
  counts `2,2,2,1` on `1001_醫事檢驗師_臨床血清免疫學與臨床病毒學`, whose equal-count rejection left
  Q71 with no options).

### 1c. A row of marks only is not always chrome

`_is_bullet_only` deletes rows made only of PUA + whitespace — but `\ue18c\ue000\ue001 \ue18d\ue000\ue002 …`
**is** a real option row. The separator is **how many distinct marks the row uses**: chrome is one
repeated drawing or a stray glyph, an option row spells alternatives with different marks, so ≥2.
Measured over the corpus: 328 mark-only rows, 27 use a single distinct code, the rest use ≥2.
Sample 60 papers before adopting: 1 improved, 0 regressed.

### 1d. The paper states what its marks mean — and which marks are defects

Two different things print as tofu. Tell them apart, and **never guess**:

- **A defined mark.** The stem states it one line above (`\ue000砂粒病毒（Arenavirus）`), and the
  options then use it (`\ue18c\ue000\ue001`). Resolve it into the paper's own words
  (`砂粒病毒（Arenavirus）+漢他病毒（Hantavirus）`) and show the legend beside the question so the
  substitution is checkable. Use `qbr/subitems.py`.
- **A lost glyph.** The mark stands *inside a word* — `轉氨\ue2c6` is 轉氨酶, `凝固\ue2c6` is 凝固酶.
  That is a **real defect at a known position**. Report its address ("第 39 個字元：凝固▢陰性"),
  show it as a defect, and never invent the character.

Rule: **a Chinese character immediately before the mark disqualifies it as a legend entry.** A mark
labels what follows it from a boundary; a lost glyph sits where an in-word character should be.
Collect lost glyphs from the **stem and every option** — a defect in an option is just as unreadable.

## 2. Deciding where a figure belongs — the band rule

> **A question owns the region from its own question number down to the next question number on the
> same page.**

- The next question's number **ends** the band. A figure below question 2 on page 1 does not belong
  to question 1.
- A figure after the last question on a page runs to the bottom of the page.
- Continuation pages with no number of their own start at `y = 0`.

This replaced a band-width rule that hid a real figure (1152 微生物 Q68), which the user reported as
"看不到圖".

## 3. Cutting the figure — object-based, never page-based

Cut the **image object**, not a rectangle of the page.

1. Get real image xrefs: `page.get_image_info(xrefs=True)`. Before this, `xref` was always `None`
   and object crops had never actually worked.
2. **Merge scanned-line slivers before measuring.** A 1041 page 3 scan was 73 objects of 2.0 pt at
   the same xref — one figure drawn as 73 strips. Recall went 91.0% → 97.1% after merging.
3. **Merging requires TWO signals: adjacency AND (same xref OR same width).**
   - xref-only regressed.
   - adjacency-only merged 1102 Q77's four electrophoresis lanes, and a twice-placed `xref=24`
     (y393.9 and y750.3, 287 pt apart) into one 425 pt box that swallowed 5–6 text questions.
   - Two signals took extras 45 → 7.
4. **Only cut what the page actually drew.** `vision.rendered_ink()` renders the region and measures
   the fraction of non-near-white pixels. A `xref=24` at 1022 p2 is placed over pure white — the
   object exists on the page but the page prints nothing there. Real figures measure 0.10–0.35 ink;
   a blank render measures **0.000**. Below `MIN_INK = 0.002` it is not a figure.
5. **A crop must be figure-only.** No text margin, no question stem, no options — the user's
   hand-cut bank is the reference and it contains only the figure.
6. **Never cut off the bottom of a figure.** This was a serious positioning error: `_box_height` /
   `_better` pick the full object box, and the band must extend to the page bottom for the last
   question on a page.
7. Minimum height `MIN_FIGURE_HEIGHT = 24.0` replaces the old area floor. **Triggers are always
   measurements, never keywords.**
8. **A figure can be emitted as hundreds of tiny image fragments, one bitmap per glyph.** Not a
   vector drawing and not one object: `1051_藥師(一)_藥劑學` Q68 prints a Lineweaver-Burk plot as
   **290 image objects, all `xref` 0, each 1–5 pt** (`get_images` returns 0, `get_drawings` returns
   only the page border). Taking the tallest fragment gave a **10×102 px sliver of the axis**.

   - Detect by **shape**, not size: `xref` falsy and both sides ≤12 pt.
   - Require a **cluster**: `MIN_FRAGMENT_COUNT = 25` in the question's band, so one mis-decoded
     glyph is not cropped as a picture.
   - Gate the fallback on the measured region: if the region is narrower than
     `MIN_FIGURE_WIDTH = 24.0`, it is not a figure. Measured over 250 papers: **all 183 real figure
     regions are ≥24 pt** (min 26.6), and the artifact is 1.4 pt — the floor sits an order of
     magnitude from both populations.

   Result: region 1.4×34.6 → **87.8×77.8 pt**, and the crop contains both axes, the diagonal, and
   the `1/V` / `1/C` / `X軸截距` labels.
9. **A bigger crop must still pass the empty-render test.** Of 21 questions the fragment rule
   enlarges, one renders at `ink = 0.00000` and must be discarded — the two rules exclude different
   errors and both are needed. "修好一層不等於修好管線."

## 4. When the answer is a picture — one crop per option

If the options are images, cut **A/B/C/D separately** and bind each to its option key.

- Bind geometrically by vertical order against the option text starts — this is measurement, not
  reading. `vision.option_figures(..., reach=OPTION_REACH)`.
- **`OPTION_REACH = 10.0` was swept, not derived**: against the hand-cut bank, reach 4.0 → 16
  correct/5 wrong, 6.0 → 17/4, **10.0 → 20 correct/1 wrong**, and ≥12 identical. Pick the smallest
  value where improvement stops.
- If the option crops together cover the whole figure, do not also emit the whole figure
  (`vision.options_cover_the_figure`).

Measured: 270 questions, 1,067 slots, 995 (93%) bound from image objects.

### 4b. "One option is one picture" is an assumption about binding, not a fact about the picture

This assumption shipped a defect that every check passed. The paper prints option C as **one**
structure; the PDF stores it as **two objects of the same width touching end to end**, and the binder
read the raw object list and kept the first:

| Option | Objects | x range | Touch at | Served crop |
|---|---|---|---|---|
| C | `xref` 16, **17** | 50.4–225.4 (identical) | y=392.4 | only 16 — **lost the `Cytotoxic drug` ellipse** |
| D | `xref` 18, **19, 20** | 50.4–388.1 (identical) | y=489.6, 529.9 | only 18 — **lost the left antibody** |

**Why no check caught it:** the crop's bytes *were* a real placed object. HTTP 200, PIL decodes,
file exists, `source=image-object`. Every check asked "is this a real object?" and the answer was
yes. **Nothing asked "is this the whole picture?"**

**The obvious fix is also wrong.** Grouping the page first fuses four options printed directly under
each other at the same width into one picture — measured on `1051` 醫事檢驗師 臨床血清免疫 Q20,
where it **destroyed 48 option crops**. Both layers are needed, in this order:

1. **Bind with the raw objects.** An option's band runs from its marker to the next marker.
2. **Merge only inside that band**, using the same width+touch rule as `picture_boxes`.
3. **The height floor selects, it does not trim.** `1081` 藥理學 Q63 B's molecule ends in two strips
   of 13.7 and 13.8 pt; applying the floor to the join cuts off the bottom of the molecule.
4. **One object → serve the object** (its own boundary). **Several → render the region**, because a
   multi-object picture has no single `xref`. Serving one strip and calling it the object is the bug.
5. **Cut from the picture's page, not the marker's.** `1081` Q63's A and B markers print on page 11
   while their structures are on page 12.

Verification of the fix: option crops **1,047 → 1,047** (0 added, 0 removed), 116 changed
(107 larger, 9 smaller), **0 regressions**. The 9 smaller ones are improvements — the old crops
carried **a whole column of other questions' structures**. And `picture_boxes`, rewritten verbatim
and compared, is **identical on 3,516/3,516 papers**, so the skeleton layer was untouched.

### 4c. One crop standard: the marker and the option's own text are part of the option

The crop of an option is **the option**, so it carries what the paper prints as that option: the
marker, the option's text, and the picture. Serving the picture's raw object bytes instead is
tempting — it is the picture's exact boundary and cannot pick up a neighbouring line — and it
silently drops the marker and the text:

| | crops | carries the marker? | carries the option's text? |
|---|---|---|---|
| `source=image-object` (raw `xref` bytes) | 866 | **no** | **no** |
| `source=page-region` (render of the box) | 183 | yes | yes |

So a reviewer moving down one question saw some options labelled and some not — the "inconsistent
standard" the user reported — and on `1152_藥師(一)_藥學(一)` Q53 the four drug names
(`alfuzosin`, `doxazosin`, `prazosin`, `terazosin`) appeared in **none** of the four crops, because
the paper prints each name beside its structure rather than inside it, and the option's *text* had
also been lost upstream (see 4d).

**The fix has two parts, and both are needed:**

1. **`option_figures` returns the option's box, not the picture's box.** Every row the skeleton
   assigned to that option — its marker *and* its body — is folded into the box, but only rows on
   the **picture's** page, because on another page a y is a different coordinate system. The
   picture's own box is still returned as `picture_box` for callers that need the boundary.
2. **The crop is always a render of that box** (`source=page-region`), never the object bytes. A
   browser cannot draw every embedded format, so the render is also the safer of the two: the 44
   JPEG 2000 option pictures needed `extract.web_safe_image()` when served as objects, and a render
   has no such problem.

Measured after the change: **1,049/1,049** option crops are `page-region`; 0 are object bytes.

### 4d. An option marker on its own line owns the next line

The typesetter sometimes sets the mark, then the option's text on the following line.
`repair.segment_mixed` is text-only and geometry-free, so the body line fell through to the stem:

```
53.下列quinazoline 類α₁-adrenergic antagonists，何者之親脂性最高…？   ← stem
  A.        ← marker, body empty
  alfuzosin ← this line went into the STEM
  B.
  doxazosin
  …
```

Result: all four options came out as `""` and the question read `…最長？ alfuzosin doxazosin
prazosin terazosin` with nothing to choose between — and, because the option text was gone, the
crop had no drug name to include either.

**Rule:** a mark with an **empty body** takes the next line as its body — and only the next line,
because a row of bare marks (`A.` `B.` `C.` `D.` with no text) is a labelled layout, not four
options waiting for bodies. A new question anchor clears the pending mark.

Measured over the corpus: **56 questions changed, 23 papers**, every change a pure relocation —
for each changed question the multiset of characters in `stem + options` is **identical**, so no
character was lost or invented. Empty option texts fell `1,153 → 1,069` (−84).

### 4e. The figure crop carries only what the option crops do not

`options_cover_the_figure` answers "are the question's pictures all option pictures?", and when it
says yes the whole-question crop is dropped as a duplicate. On `1141_藥師(一)_藥學(一)` Q41 the
question has **five** pictures — the stem's ring drawing plus one structure per option — so the
test said no and a crop was made, and that crop contained **all four options a second time**,
including the answer. The user reported it as "the crop caught the answer".

**The test was right; the crop's contents were wrong.** The fix is an `exclude` argument: the
option pictures' boxes are removed from the figure region, so the crop holds only the stem's
picture. Two consequences worth stating, because the tempting shortcuts are both wrong:

- **Do not narrow `options_cover_the_figure` instead.** Treating the stem picture as "already
  covered" answers `True` and drops the stem's picture from the pack altogether. Measured: this
  variant added 98 crops whose content was other questions' option pictures.
- **`figure_region` returning `None` must not fall back to the whole question.** With every picture
  excluded there is nothing to cut; falling back to `entry["box"]` cuts the text and the option
  pictures, which is the redundancy being removed. `crop_figure` now returns `no-figure`, and the
  caller treats that as "the option crops carry the figure", not as an error.

Measured: figure crops **958 → 956**, option crops **1,049 → 1,049**, and
`verify_crops` reports **0 incomplete** over the whole corpus.

## 4f. Super/subscripts: the size ratio is a floor, the baseline shift decides

A line is read span by span precisely so `B2` and `B₂` do not come out identical. That protection
was off for most of the corpus because the gate was a **size ratio measured on one style**:
`_OFFSET_MAX_SIZE_RATIO = 0.75`, from 5.5pt against an 11pt body.

This corpus sets chemical subscripts at **0.82–0.85** of the body size, so `CH3(CH2)11OR` was
extracted as `CH3(CH2)11OR` with the digits on the baseline. Measured over the four categories:
145,639 spans are smaller than their line's body, and of those at ratio ≥ 0.78, **68,431 sit at
dcy 0 or −1** (ordinary smaller text) while **10,684 sit at +2 to +4** (the formulas).

**Two styles, so two thresholds.** A single pair cannot serve both:

| style | sizes | ratio | measured shift | examples |
|---|---|---|---|---|
| tight | 5.5pt / 11pt | 0.50 | `³⁻` −3.87, `₄` +1.66 | `[K⁺]－[PO₄³⁻]－[HCO₃⁻]` |
| loose | 7.4pt / 9pt | 0.82 | `₃` +2.0, `₂` +3.8 | `CH₃(CH₂)₁₁OR` |

At `0.75/±1.0` the loose style is flattened; at `0.90/±2.0` the tight style is. The test is the
**union** of the two, and the body's own ratio (`_BODY_SIZE_RATIO = 0.75`) is kept **separate** from
the admit floor (`_OFFSET_MAX_SIZE_RATIO = 0.90`) — widening one constant to do both jobs lets a
0.82-ratio subscript pull the baseline toward itself and hide the very spans being looked for.

**Require every character to be mappable, not just one.** `any()` corrupts: a span mixing mappable
and unmappable characters gets *half* converted, and over the corpus that turned the digits of
ordinary numbers into superscripts — `41.` → `⁴¹.`, `1c` → `₁c`, `-0.2t` → `⁻⁰.²t`, **240 spans**.
With `all()`, **4,546** runs convert and **4,550** do not; the ones that do not are the option
markers (`A.`/`B.`/`C.`/`D.`, which must not convert), `®`, and symbols with no subscript form
(`max`, `p`, `M`, `Cr`). Leaving those alone is honest; half-converting them is not.

Acceptance over the whole corpus: **4,035 subscript and 2,249 superscript characters**, **0** cases
of a digit run followed by `.` being converted, and **3,516/3,516** papers in two-engine agreement.

## 5. Truncation and band width

- Band width is the **content width**, not the page width and not an object's width. An object
  narrower than the text truncated figures. Measured over 4,662 pages: truncation 33 → 0, max
  horizontal gap 0.0 pt.
- **Truncation must be an error.** A silently truncated figure looks complete and is not.

## 5b. The container is part of the crop — a file that exists and still shows nothing

A crop can be on disk, referenced, HTTP 200, byte-identical to the PDF's own object, **and still
render as an empty box**. Measured: 44 option pictures in this corpus are **JPEG 2000** (`jpx`).
Chrome does not decode JPX under any declared type, so the reviewer saw the alt text beside a broken
image.

- **Normalize the container when the crop is written**, not when it is displayed. Pass through
  formats a browser draws (`png/jpg/jpeg/gif/webp/bmp`) untouched — the hand-cut reference bank is
  byte-identical to the embedded object, and re-encoding breaks that correspondence.
- **Let the bytes decide the MIME type, and the file name be only the fallback.** An extension is a
  claim. Serving `image/png` for JPEG bytes makes Chrome refuse to draw it. This cannot make a bad
  image good; it only stops the server from actively mislabelling one.

**How to test it, and how not to.** A `curl` returning 200 proves nothing — all 2,148 requests
returned 200 while 40 images were undrawable. Load every crop as an `<img>` in a real browser and
count `naturalWidth == 0`. On the full corpus: **1992/1992 load, 0 broken.**

## 5c. Do not ask a model whether a crop is good

Asked "is this clipped?", the model flagged **56 option crops — and every one was a byte-identical
copy of the PDF's embedded object, which cannot be clipped by construction.** Asked "how many
structures?" about the same option image repeatedly, it answered 1, 2, and 0; **39 of 131 repeat
measurements disagreed with themselves (30%)**.

This was re-measured as a full experiment, because the request to use a model is a reasonable one
and deserves a real answer rather than a recollection:

| Experiment | Result |
|---|---|
| 40 real broken crops + 40 real fixed ones, crop shown beside the same page region | **80/80 answered `same=true`** — recall 0%, false positives 0% |
| Positive control: keep only the **top 8 pt** of a structure, so the image is nearly empty | still **7 of 8 answered `same=true`** |
| 400 served crops, same test | 0 flagged |

- **A model that says "fine" to a nearly-empty strip is not checking.** The control is what proves
  it: without a case designed to be *detected*, "no problems found" cannot be told from "no
  problems looked for".
- **A model is for reading, not for judging quality.** Use it where the ground truth is unavailable
  and a human checks the result; not where a byte comparison already answers the question.
- **A self-contradicting measurement is not a weak measurement, it is not a measurement.** Run the
  same input twice; if the answer changes, the answer was noise.
- **Check the claim against the bytes.** `source=image-object` means the bytes came from the PDF, so
  no edge is missing. This is a proof, not an opinion, and it settles the question in one comparison.

### 5c-1. What actually verifies a crop: the page's own strip structure

A model cannot answer "is this complete", but the file can, because **a picture split into strips
is a fact recorded in the PDF**:

```
one object of a picture  ->  the next strip has the same width and starts where this one ends
```

**The rule, and every bound on it, was measured:**

| Condition | Why it is needed |
|---|---|
| same width (±0.5 pt) | Adjacency alone joins four electrophoresis lanes 20.7 pt apart; two pictures differ in width |
| touching (±1 pt) | The identity is "where the last one ended" |
| height ≥ 12 pt | A producer emits 0.8 pt rules as objects; `1091` Q53 C and `1082` Q73 C are two |
| **inside the same option's band** | Without it, the strip below is just as likely to be the **next option's picture**: `1091` Q53 C's is option D's formula, `1071` Q76 B's is option C's structure. 5 false positives in 40 |
| band bound = the next marker, **on the picture's page** | A cross-page marker is not a bound: `1071` Q76 B's structure legitimately runs past `C.` which prints on the previous page |

**Do not use ink outside the box for this.** It was tried and measured wrong twice: `1012` 藥理學
Q46 D's exterior ink is 0.18 and all of it is the *next question's stem text*; `1072` 臨床血清免疫
Q8's is 0.99 and all of it is the *next option's diagram*. Excluding text spans does not help — a
neighbouring structure is still a structure. 13 of 120 flagged, **none a real defect**.

Acceptance after the rule was narrowed: **19/19 known broken crops caught, 0/400 false positives,
0/2,007 incomplete across the corpus.**

## 5d. Showing the crop is part of the crop being right

- **An option picture is the option.** A 220x200 thumbnail of a chemical structure hides the bonds
  and subscripts that are the answer. Allow it its natural size within a bound, and let a click open
  it full size.
- **Do not re-render the panel on every navigation.** Replacing `innerHTML` re-creates every `<img>`,
  throwing away the browser's decoded copy and re-fetching each crop. Measured: the per-question
  request cost **1.2 MB and 0.72 s per keypress** and its result was never read.

- **A corrections sheet is a re-issued table, not only a note.** So the accepted answers are joined
  with 「或」 and may name a combination (`B或BC或C`). Splitting that string on commas finds **no**
  option and nothing is highlighted — the question looks like it has no answer. Read
  `answer_payload.accepted_values` (it is present on every row), and accept both separators as a
  fallback. Measured: 393 questions carry a non-single answer; 266 name more than one thing.

## 6. Writing the result — the two defects that make good crops invisible

1. **A blank region must clear its references.** If ink < `MIN_INK`, write a record with
   `blank_render: True` and set `image_refs` to `[]`, then `continue`. Skipping the write left a
   previous run's reference pointing at a file this run correctly did not produce.
2. **Rewrite EVERY row's `image_refs` from this run's result**, including to `[]`
   (`want = refs.get(row["candidate_key"], [])`). Measured: this fixed 48 stale refs across 18
   papers; bad refs 48 → 0.

A run must be self-contained: the merged queue owns its crops, and the review UI serves them under
an allowed root.

## 7. Verifying — against something you did not write

**Do not validate the pipeline with data the pipeline produced.** The workspace's hand-cut bank
(`40_manual_assets/question_images/`, and the packaged
`tw-national-exam-medtech-v2026.08.04-r1`) is the only standard here that the tested program did not
write, which is what makes it worth using.

Scoring rules that matter:

- **A miss IS an error.** A figure the bank has and you did not find is a real defect.
- **An extra is NOT necessarily an error.** The bank not covering a question is not the pipeline
  being wrong. Of 7 extras, all 7 were real figures the bank simply did not include (1012 Q43 flow
  cytometry, 1022 Q18 marrow aspirate, 1041 Q18 enzyme kinetics, 1052 Q26 lung volumes, 1102 Q22
  flow-volume loop). **Never delete a real figure to make a number look good.**
- **Accept "no single answer."** An official answer of `送分` or `A或D` is not a model error. Score
  only questions with a single-letter answer, or every metric will be wrong.
- **An empty answer is a budget artefact, not a model failure.** Check for empty before scoring.

Measured: bank_with_figure 455, agree 442 (**97.1%**), miss 13, extra 5, option agree 20 / mismatch 2.
Of the 13 misses, 2 are **vector-drawn figures** (e.g. 1011 Q70, 8 drawing objects, no embedded
image) — a class not yet implemented, not a class done wrong.

## 8. Measuring a free parameter, and re-running after a fix

- **Sweep, do not derive.** Reach (4→30), `MIN_INK`, `MIN_FIGURE_HEIGHT` and merge tolerance were
  all swept; pick the smallest value where improvement stops.
- **Measure what the competing rule would drop BEFORE adopting it.** The two-signal merge was
  adopted only after measuring that xref-only and adjacency-only each destroyed real figures.
- **A green test can protect a wrong rule.** Two tests asserted the band rule that hid Q68; they had
  to be replaced, not satisfied.
- **A fix at one layer must be re-run, because the previous layer's error hides in the next layer's
  output.** Fixing the ink check exposed the 48 stale refs.
- **Separate measuring from interpreting.** Report the number first, the meaning second.

## 9. Question groups — several questions, one printed stem

A paper prints one setup paragraph, then two or more questions about it. The later ones say so only
as `承上題`. Measured over the queue: **441 questions open with a continuation marker**, forming 429
runs (414 of length 1, 15 of length 2); with all markers, **871 questions in 413 groups**.

- **Bind the group while segmenting the paper, not afterwards from finished text.** The grouping is
  a property of the paper, and a post-pass would have to guess the boundary back from the text —
  which is exactly what is damaged when the split is wrong.
- **Group by the paper's own signal** (a continuation marker at the start of the stem). A marker in
  the middle of a sentence is the question's own words, not the paper's structure.
- **Carry the shared stem onto every member, not only the head.** A continuation without the stem it
  continues from is unjudgeable: `承上題，達穩定狀態之平均血中濃度約為多少mg/L？` needs the paragraph.
- **Mark it rather than paste it in.** The shared stem is the head's stem as printed; pasting it into
  the member would claim the paper printed it there.
- **The unit is the paper.** Grouping across papers would attach a first question's `承上題` to the
  previous paper's last question.
- **Do not ask a model to decide the boundary.** A model asked "do these share a stem?" answers
  plausibly and unverifiably. A model is useful for *reading* a damaged stem, not for deciding a
  boundary the page already states.

## 9b. The reviewer's decisions live in the queue, so a rebuild must carry them

This is data loss, and it is silent. Review events are written by the UI into the **queue
directory** — `review-ui/question_review_events.jsonl` — and rebuilding the queue into a fresh
directory writes a new, empty file beside 33,150 fresh rows. The reviewer's 51 decisions are gone
and nothing errors; the queue is simply back to unreviewed.

**Why "self-contained" made it worse.** `review_run.sh` documents the run directory as
self-contained, which is true and is exactly the trap: self-contained means *a rebuild discards it*.
Older queues (`qbr-live-final`, `-stripfix`, `-stripfix2`) had **no events file at all**, so the
loss had already happened repeatedly and looked like "nobody reviewed anything".

The fix is a carry step in `build_review_queue.py`:

- **All six event streams**, not just the question one: `question_review_events.jsonl`,
  `answer_review_events.jsonl`, `question_ai_review_events.jsonl`,
  `question_ai_feedback_events.jsonl`, `question_ai_learning_events.jsonl`,
  `question_correction_feedback_events.jsonl`.
- **`--carry-from` defaults to `--out` itself**, so a rebuild in place cannot lose anything even
  when nobody remembers the flag. Passing the *old* queue as well merges the two — that is how a
  rebuild into a new directory keeps the previous queue's history.
- **Dedupe, and report `carried` vs `orphaned`.** A record whose `candidate_key` is not in the new
  queue is reported, not silently dropped; orphaned does not mean wrong.
- **Truncate (`"w"`), never append (`"a"`).** The first version appended, so an in-place rebuild
  read the file and appended it to itself: **45 records became 90**. This is the bug that makes the
  fix look like it works while doubling every rebuild.

Acceptance: an in-place rebuild is **idempotent** — 51 records stable across 3 successive rebuilds —
and every record in the source is present in the carried set. Also: **the improved UI replaces the
previous one**. `v2.html` is edited in place rather than forked, because the reviewer's decisions are
keyed by `candidate_key` and a second UI would be a second place for them to diverge.

## 10. Acceptance

- Two engines agree on the question count for every paper. **Measured over the whole corpus:
  3,516/3,516.**
- Every crop is referenced, on disk, HTTP-200, and decodable.
- Zero truncation.
- Compared against the hand-cut bank, with misses and extras reported separately.
- The review UI shows the crop, at the question it belongs to.
- **No question is withheld for a class the rule can describe.** 429/429 packaged, 0 blocked.
  "All pass" is not the same as "tested": a paper blocked as "the same class" as nine others
  must be shown to share the *cause*, not only the complaint. The ten blocked papers turned out
  to be **four** classes (two alphabets / a deleted option row / sub-item marks / a wrapped line).
- **Every unreadable character is addressed.** A lost glyph may never be silently approved; the
  row must carry the position and the word it stands in. Measured: 0 unannotated lost glyphs.
- **A mark the paper defines is shown with its legend**, so a reviewer can check the substitution
  rather than trust it.
- **A rebuild never loses a reviewer's decision.** Measured: 51/51 carried, 0 orphaned, idempotent
  across successive rebuilds.
- **Super/subscripts survive.** Measured: 4,035 subscript and 2,249 superscript characters over the
  four categories, 0 corrupt conversions.
- **One crop standard.** Measured: 1,049/1,049 option crops are rendered regions carrying the marker
  and the option's text; 0 are raw object bytes.

## Reference implementation

`qbr/` — `src/qbr/reflow.py` (skeleton, band), `src/qbr/vision.py`
(`picture_boxes`, `rendered_ink`, `option_figures`, `figure_region`), `src/qbr/extract.py`
(`image_bytes_of`), `scripts/crop_run_figures.py`, `scripts/compare_manual_assets.py`,
`scripts/measure_speed.py`. Rationale and every measurement: `qbr/ENGINE_STRATEGY.md` §18–26.

The pipeline moved out of the sandbox (`pi_test/question_bank_rebuild/`, now kept as archaeology)
into the mainline at `tw-national-exam-catalog/qbr/`; the paths above are the current ones.
